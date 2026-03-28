"""
Plan Agent
==========
Decomposes a high-level user request into a structured graph of sub-tasks
using a LangChain + Gemini ReAct chain.

Exposes:
  POST /a2a  — A2A endpoint (action: "decompose")
  GET  /healthz, /readyz, /info
"""

import json
import logging
import os
import sys
import re
import time

import uvicorn
from fastapi import FastAPI
from langchain.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from prometheus_client import Counter, Histogram

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from shared.a2a_protocol import create_a2a_handler
from shared.llm_client import get_llm
from shared.utils import attach_health_routes, mount_metrics_endpoint, setup_logging

logger = setup_logging("plan_agent")
app = FastAPI(title="Plan Agent", version="1.0.0")
attach_health_routes(app, "plan_agent", version="1.0.0")
mount_metrics_endpoint(app)

REQUESTS = Counter(
    "qagent_agent_requests_total",
    "Requests processed by this agent",
    ["agent", "action", "status"],
)
DURATION = Histogram(
    "qagent_agent_duration_seconds",
    "Request processing time",
    ["agent", "action"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
)

llm = get_llm(temperature=0.1)

# ──────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = """You are a planning expert for a multi-agent AI system.
Your job is to decompose a user's request into a small set of independent,
concrete sub-tasks that can each be handled by a specialist code-generation agent.

Rules:
- Produce between 2 and 5 tasks. Never more.
- Each task must be self-contained and implementable in isolation.
- Return ONLY valid JSON — no markdown fences, no commentary.

Output format:
{{
  "summary": "<one sentence plan summary>",
  "complexity": "low|medium|high",
  "tasks": [
    {{
      "id": "T1",
      "description": "<concrete, actionable task description>",
      "language": "python|javascript|yaml|bash",
      "depends_on": []
    }}
  ]
}}"""

DECOMPOSE_HUMAN = """User request: {request}

Additional context: {context}

Decompose this into sub-tasks."""

decompose_chain = (
    ChatPromptTemplate.from_messages([
        ("system", DECOMPOSE_SYSTEM),
        ("human", DECOMPOSE_HUMAN),
    ])
    | llm
    | StrOutputParser()
)

# ──────────────────────────────────────────────────────────────────
# Action handlers
# ──────────────────────────────────────────────────────────────────

async def _decompose(payload: dict) -> dict:
    request_text = payload.get("request", "")
    context = payload.get("context", {})

    logger.info("Decomposing request: %s", request_text[:100])
    t0 = time.perf_counter()

    try:
        raw = await decompose_chain.ainvoke({
            "request": request_text,
            "context": json.dumps(context) if context else "none",
        })

        # Strip potential markdown fences
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()

        try:
            plan = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, returning raw fallback")
            plan = {
                "summary": "Could not parse structured plan",
                "complexity": "medium",
                "tasks": [{"id": "T1", "description": request_text, "language": "python", "depends_on": []}],
            }

        REQUESTS.labels("plan_agent", "decompose", "success").inc()
        logger.info("Plan: %d tasks, complexity=%s", len(plan.get("tasks", [])), plan.get("complexity"))
        return plan
    except Exception:
        REQUESTS.labels("plan_agent", "decompose", "error").inc()
        raise
    finally:
        DURATION.labels("plan_agent", "decompose").observe(time.perf_counter() - t0)


# ──────────────────────────────────────────────────────────────────
# A2A route
# ──────────────────────────────────────────────────────────────────

app.add_api_route(
    "/a2a",
    create_a2a_handler("plan_agent", {"decompose": _decompose}),
    methods=["POST"],
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
