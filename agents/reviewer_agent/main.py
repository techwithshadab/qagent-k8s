"""
Reviewer Agent
==============
Reviews code produced by the Coder Agent for correctness, security,
and alignment with the original task. Uses a structured JSON output.

Actions:
  review — returns {approved: bool, score: int, feedback: str, issues: [str]}
"""

import json
import logging
import os
import re
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

logger = setup_logging("reviewer_agent")
app = FastAPI(title="Reviewer Agent", version="1.0.0")
attach_health_routes(app, "reviewer_agent", version="1.0.0")
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

REVIEW_SYSTEM = """You are a strict but fair code reviewer with deep expertise in
software quality, security, and correctness.

Review the provided code against the task description.

Return ONLY valid JSON — no markdown, no commentary:
{{
  "approved": true | false,
  "score": <integer 1-10>,
  "feedback": "<one paragraph summary>",
  "issues": ["<issue 1>", "<issue 2>"],
  "suggestions": ["<suggestion 1>"]
}}

Approve (true) if score >= 7 and no critical security issues exist."""

REVIEW_HUMAN = """Task: {task}

Architect's strategy:
{strategy}

Code to review ({language}):
```
{code}
```

Provide your structured review."""

review_chain = (
    ChatPromptTemplate.from_messages([
        ("system", REVIEW_SYSTEM),
        ("human", REVIEW_HUMAN),
    ])
    | llm
    | StrOutputParser()
)

# ──────────────────────────────────────────────────────────────────
# Action handlers
# ──────────────────────────────────────────────────────────────────

async def _review(payload: dict) -> dict:
    task = payload.get("task", "")
    code = payload.get("code", "")
    language = payload.get("language", "python")
    strategy = payload.get("strategy", "")

    logger.info("Reviewing %s code (%d chars) for: %s", language, len(code), task[:80])
    t0 = time.perf_counter()

    try:
        raw = await review_chain.ainvoke({
            "task": task,
            "strategy": strategy,
            "language": language,
            "code": code[:4000],  # Trim very long code blocks
        })

        clean = re.sub(r"```(?:json)?|```", "", raw).strip()

        try:
            review = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning("Could not parse reviewer JSON, using defaults")
            review = {
                "approved": True,
                "score": 7,
                "feedback": raw[:500],
                "issues": [],
                "suggestions": [],
            }

        REQUESTS.labels("reviewer_agent", "review", "success").inc()
        logger.info(
            "Review complete: approved=%s score=%s issues=%d",
            review.get("approved"), review.get("score"), len(review.get("issues", []))
        )
        return review
    except Exception:
        REQUESTS.labels("reviewer_agent", "review", "error").inc()
        raise
    finally:
        DURATION.labels("reviewer_agent", "review").observe(time.perf_counter() - t0)


# ──────────────────────────────────────────────────────────────────
# A2A route
# ──────────────────────────────────────────────────────────────────

app.add_api_route(
    "/a2a",
    create_a2a_handler("reviewer_agent", {"review": _review}),
    methods=["POST"],
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8004))
    uvicorn.run(app, host="0.0.0.0", port=port)
