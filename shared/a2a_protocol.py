"""
A2A (Agent-to-Agent) protocol client and data models.

Implements a lightweight A2A-compatible HTTP protocol so each agent
pod can call other agent pods via their Kubernetes service names.

Wire format (JSON):
{
    "task_id":    "<uuid>",
    "from_agent": "<agent_name>",
    "to_agent":   "<agent_name>",
    "action":     "<action>",
    "payload":    { ... },
    "metadata":   { ... }
}

Response:
{
    "task_id":  "<uuid>",
    "status":   "success" | "error",
    "result":   { ... },
    "error":    "<message if status==error>"
}
"""

import os
import time
import uuid
import logging
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# A2A metrics
# ──────────────────────────────────────────────────────────────────

A2A_REQUESTS = Counter(
    "qagent_a2a_requests_total",
    "Total A2A calls by agent pair and outcome",
    ["from_agent", "to_agent", "action", "status"],
)
A2A_DURATION = Histogram(
    "qagent_a2a_duration_seconds",
    "A2A call latency in seconds",
    ["from_agent", "to_agent", "action"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
)


# ──────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────

class A2ARequest(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    from_agent: str
    to_agent: str
    action: str
    payload: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}


class A2AResponse(BaseModel):
    task_id: str
    status: str          # "success" | "error"
    result: Dict[str, Any] = {}
    error: Optional[str] = None


# ──────────────────────────────────────────────────────────────────
# Service discovery — reads from env vars set by Kubernetes
# ──────────────────────────────────────────────────────────────────

AGENT_ENDPOINTS: Dict[str, str] = {
    "orchestrator":  os.environ.get("ORCHESTRATOR_SVC_URL",  "http://orchestrator-svc:8000"),
    "plan_agent":    os.environ.get("PLAN_AGENT_SVC_URL",    "http://plan-agent-svc:8001"),
    "advisor_agent": os.environ.get("ADVISOR_AGENT_SVC_URL", "http://advisor-agent-svc:8002"),
    "coder_agent":   os.environ.get("CODER_AGENT_SVC_URL",   "http://coder-agent-svc:8003"),
    "reviewer_agent":os.environ.get("REVIEWER_AGENT_SVC_URL","http://reviewer-agent-svc:8004"),
}


# ──────────────────────────────────────────────────────────────────
# A2A client
# ──────────────────────────────────────────────────────────────────

class A2AClient:
    """
    Sends A2A requests to other agent pods.

    Usage:
        client = A2AClient(from_agent="orchestrator")
        response = await client.call("plan_agent", action="decompose", payload={"request": "..."})
    """

    def __init__(self, from_agent: str, timeout: float = 60.0):
        self.from_agent = from_agent
        self.timeout = timeout

    async def call(
        self,
        to_agent: str,
        action: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> A2AResponse:
        base_url = AGENT_ENDPOINTS.get(to_agent)
        if not base_url:
            raise ValueError(f"Unknown agent: {to_agent}. Available: {list(AGENT_ENDPOINTS)}")

        request = A2ARequest(
            from_agent=self.from_agent,
            to_agent=to_agent,
            action=action,
            payload=payload,
            metadata=metadata or {},
        )

        url = f"{base_url}/a2a"
        logger.info("A2A call  %s → %s  action=%s  task_id=%s",
                    self.from_agent, to_agent, action, request.task_id)

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=request.model_dump())
                response.raise_for_status()
                data = response.json()
            resp = A2AResponse(**data)
        except Exception:
            elapsed = time.perf_counter() - t0
            A2A_REQUESTS.labels(self.from_agent, to_agent, action, "error").inc()
            A2A_DURATION.labels(self.from_agent, to_agent, action).observe(elapsed)
            raise

        elapsed = time.perf_counter() - t0
        A2A_DURATION.labels(self.from_agent, to_agent, action).observe(elapsed)
        A2A_REQUESTS.labels(self.from_agent, to_agent, action, resp.status).inc()

        if resp.status == "error":
            logger.error("A2A error from %s: %s", to_agent, resp.error)
        else:
            logger.info("A2A success from %s  task_id=%s", to_agent, resp.task_id)

        return resp


# ──────────────────────────────────────────────────────────────────
# FastAPI router mixin — add to any agent to expose /a2a endpoint
# ──────────────────────────────────────────────────────────────────

def create_a2a_handler(agent_name: str, action_map: Dict[str, Any]):
    """
    Factory: returns a FastAPI-compatible async handler function.

    action_map = {
        "action_name": async_function(payload) -> dict
    }
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    async def a2a_handler(request: Request):
        body = await request.json()
        a2a_req = A2ARequest(**body)

        action_fn = action_map.get(a2a_req.action)
        if action_fn is None:
            return JSONResponse(
                status_code=400,
                content=A2AResponse(
                    task_id=a2a_req.task_id,
                    status="error",
                    error=f"Unknown action: {a2a_req.action}. Available: {list(action_map)}",
                ).model_dump(),
            )

        try:
            result = await action_fn(a2a_req.payload)
            return JSONResponse(
                content=A2AResponse(
                    task_id=a2a_req.task_id,
                    status="success",
                    result=result,
                ).model_dump()
            )
        except Exception as exc:
            logger.exception("Error handling action %s", a2a_req.action)
            return JSONResponse(
                status_code=500,
                content=A2AResponse(
                    task_id=a2a_req.task_id,
                    status="error",
                    error=str(exc),
                ).model_dump(),
            )

    return a2a_handler


__all__ = ["A2AClient", "A2ARequest", "A2AResponse", "create_a2a_handler", "AGENT_ENDPOINTS"]
