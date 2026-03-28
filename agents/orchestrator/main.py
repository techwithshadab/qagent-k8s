"""
Orchestrator Agent
==================
The central coordinator of the QAgent-K8s multi-agent system.

Responsibilities:
  1. Accept user requests via HTTP POST /run
  2. Call PlanAgent to decompose the request into sub-tasks
  3. Route each sub-task to the appropriate specialist agent (Advisor → Coder → Reviewer)
  4. Aggregate results and return a structured final report

Each inter-agent call uses the A2A protocol (a2a_protocol.py).
The agent itself is a FastAPI app — one pod in Kubernetes.

Environment variables (set via Kubernetes Secret / ConfigMap):
  GEMINI_API_KEY      — Google Gemini API key
  GEMINI_MODEL        — model name (default: gemini-1.5-flash)
  LOG_LEVEL           — DEBUG | INFO | WARNING (default: INFO)
  PORT                — HTTP port (default: 8000)
"""

import asyncio
import logging
import os
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Add parent dir to path so shared/ is importable
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prometheus_client import Counter, Histogram

from shared.a2a_protocol import A2AClient, create_a2a_handler
from shared.utils import attach_health_routes, mount_metrics_endpoint, setup_logging

logger = setup_logging("orchestrator")

# ──────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────

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

# ──────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Orchestrator Agent",
    description="Top-level coordinator for QAgent-K8s multi-agent system",
    version="1.0.0",
)
attach_health_routes(app, "orchestrator", version="1.0.0")
mount_metrics_endpoint(app)

a2a_client = A2AClient(from_agent="orchestrator")


# ──────────────────────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    user_request: str
    context: Dict[str, Any] = {}


class RunResponse(BaseModel):
    status: str
    plan: Dict[str, Any]
    results: List[Dict[str, Any]]
    final_report: str


# ──────────────────────────────────────────────────────────────────
# Core orchestration logic
# ──────────────────────────────────────────────────────────────────

async def _run_task_pipeline(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    For a single sub-task, run the Advisor → Coder → Reviewer pipeline.

    Best practice: keep individual agent calls idempotent so the
    orchestrator can safely retry on transient failures.
    """
    task_id = task.get("id", "unknown")
    task_description = task.get("description", "")

    logger.info("Running pipeline for task %s: %s", task_id, task_description[:80])

    # 1. Advisor: get high-level strategy
    advisor_resp = await a2a_client.call(
        to_agent="advisor_agent",
        action="advise",
        payload={"task": task_description, "context": task.get("context", {})},
    )
    if advisor_resp.status == "error":
        return {"task_id": task_id, "status": "error", "error": advisor_resp.error}

    strategy = advisor_resp.result.get("strategy", "")

    # 2. Coder: generate solution using advisor's strategy
    coder_resp = await a2a_client.call(
        to_agent="coder_agent",
        action="generate",
        payload={
            "task": task_description,
            "strategy": strategy,
            "context": task.get("context", {}),
        },
    )
    if coder_resp.status == "error":
        return {"task_id": task_id, "status": "error", "error": coder_resp.error}

    generated_code = coder_resp.result.get("code", "")
    language = coder_resp.result.get("language", "python")

    # 3. Reviewer: validate the generated code
    reviewer_resp = await a2a_client.call(
        to_agent="reviewer_agent",
        action="review",
        payload={
            "task": task_description,
            "code": generated_code,
            "language": language,
            "strategy": strategy,
        },
    )

    return {
        "task_id": task_id,
        "description": task_description,
        "status": "success",
        "strategy": strategy,
        "code": generated_code,
        "language": language,
        "review": reviewer_resp.result if reviewer_resp.status == "success" else {},
        "approved": reviewer_resp.result.get("approved", False),
    }


def _build_final_report(
    user_request: str,
    plan: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> str:
    """Assemble a human-readable Markdown report from all task results."""
    all_approved = all(r.get("approved", False) for r in results if r.get("status") == "success")
    status_emoji = "✅" if all_approved else "⚠️"

    lines = [
        f"# QAgent-K8s Workflow Report",
        f"",
        f"**Overall Status:** {status_emoji} {'All tasks approved' if all_approved else 'Some tasks need review'}",
        f"",
        f"## User Request",
        f"> {user_request}",
        f"",
        f"## Decomposed Tasks",
    ]

    for task in plan.get("tasks", []):
        lines.append(f"- **{task.get('id')}**: {task.get('description', '')}")

    lines += ["", "## Results"]

    for result in results:
        tid = result.get("task_id", "?")
        approved = "✅ Approved" if result.get("approved") else "⚠️ Needs revision"
        lines += [
            f"",
            f"### Task {tid}  {approved}",
            f"**Strategy:** {result.get('strategy', 'N/A')[:200]}",
            f"",
            f"```{result.get('language', 'python')}",
            result.get("code", "# no code generated"),
            "```",
        ]
        review = result.get("review", {})
        if review.get("feedback"):
            lines += [f"", f"**Review:** {review['feedback']}"]

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────
# HTTP endpoints
# ──────────────────────────────────────────────────────────────────

@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest):
    """
    Main entry point. Accepts a user request, orchestrates all agents,
    and returns a structured report.
    """
    import time as _time
    _t0 = _time.perf_counter()
    logger.info("Received request: %s", req.user_request[:120])

    try:
      # Step 1: Plan decomposition
      plan_resp = await a2a_client.call(
          to_agent="plan_agent",
          action="decompose",
          payload={"request": req.user_request, "context": req.context},
      )
      if plan_resp.status == "error":
          REQUESTS.labels("orchestrator", "run", "error").inc()
          DURATION.labels("orchestrator", "run").observe(_time.perf_counter() - _t0)
          raise HTTPException(status_code=500, detail=f"PlanAgent error: {plan_resp.error}")
    except HTTPException:
        raise
    except Exception:
        REQUESTS.labels("orchestrator", "run", "error").inc()
        DURATION.labels("orchestrator", "run").observe(_time.perf_counter() - _t0)
        raise

    plan = plan_resp.result

    # Step 2: Execute tasks in parallel (best practice for independent tasks)
    tasks = plan.get("tasks", [])
    logger.info("Plan has %d tasks", len(tasks))

    pipeline_coroutines = [_run_task_pipeline(task) for task in tasks]
    results = await asyncio.gather(*pipeline_coroutines, return_exceptions=False)

    # Step 3: Build report
    final_report = _build_final_report(req.user_request, plan, results)

    REQUESTS.labels("orchestrator", "run", "success").inc()
    DURATION.labels("orchestrator", "run").observe(_time.perf_counter() - _t0)
    return RunResponse(
        status="success",
        plan=plan,
        results=results,
        final_report=final_report,
    )


# ──────────────────────────────────────────────────────────────────
# A2A endpoint (orchestrator can also receive A2A calls)
# ──────────────────────────────────────────────────────────────────

async def _handle_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "running", "agent": "orchestrator"}


app.add_api_route(
    "/a2a",
    create_a2a_handler("orchestrator", {"status": _handle_status}),
    methods=["POST"],
)


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
