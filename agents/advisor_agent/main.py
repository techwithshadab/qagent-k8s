"""
Advisor Agent
=============
Provides high-level coding strategy and architectural guidance for a task
before the Coder Agent writes any code.

Actions:
  advise — returns a strategy string given a task description
"""

import logging
import os
import sys
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

logger = setup_logging("advisor_agent")
app = FastAPI(title="Advisor Agent", version="1.0.0")
attach_health_routes(app, "advisor_agent", version="1.0.0")
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

llm = get_llm(temperature=0.3)

# ──────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────

ADVISE_SYSTEM = """You are a senior software architect and coding strategist.
Given a task description, provide concise, actionable guidance that a code
generation agent will use to produce correct, idiomatic code.

Your advice must cover:
1. The recommended approach / algorithm / pattern
2. Key edge cases to handle
3. Libraries or built-ins to prefer (or avoid)
4. Target code quality: clarity over cleverness

Keep your response under 200 words. Plain text, no markdown headers."""

ADVISE_HUMAN = """Task: {task}

Context: {context}

Provide your coding strategy."""

advise_chain = (
    ChatPromptTemplate.from_messages([
        ("system", ADVISE_SYSTEM),
        ("human", ADVISE_HUMAN),
    ])
    | llm
    | StrOutputParser()
)

# ──────────────────────────────────────────────────────────────────
# Action handlers
# ──────────────────────────────────────────────────────────────────

async def _advise(payload: dict) -> dict:
    task = payload.get("task", "")
    context = payload.get("context", {})

    logger.info("Advising on task: %s", task[:80])
    t0 = time.perf_counter()

    try:
        strategy = await advise_chain.ainvoke({
            "task": task,
            "context": str(context) if context else "none",
        })
        REQUESTS.labels("advisor_agent", "advise", "success").inc()
        logger.info("Strategy generated (%d chars)", len(strategy))
        return {"strategy": strategy.strip()}
    except Exception:
        REQUESTS.labels("advisor_agent", "advise", "error").inc()
        raise
    finally:
        DURATION.labels("advisor_agent", "advise").observe(time.perf_counter() - t0)


# ──────────────────────────────────────────────────────────────────
# A2A route
# ──────────────────────────────────────────────────────────────────

app.add_api_route(
    "/a2a",
    create_a2a_handler("advisor_agent", {"advise": _advise}),
    methods=["POST"],
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port)
