"""
Coder Agent
===========
Generates code for a given task using the strategy provided by the Advisor.
Uses a LangChain + Gemini chain with a structured output prompt.

Actions:
  generate — returns generated code + language
"""

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

logger = setup_logging("coder_agent")
app = FastAPI(title="Coder Agent", version="1.0.0")
attach_health_routes(app, "coder_agent", version="1.0.0")
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

llm = get_llm(temperature=0.2)

# ──────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────

CODE_SYSTEM = """You are an expert software engineer. Write clean, production-ready code.

Rules:
- Output ONLY the code — no explanations, no markdown fences.
- Include docstrings and inline comments for non-obvious logic.
- Handle errors gracefully.
- Follow the language's idiomatic style.
- Implement exactly what is asked — no extra features."""

CODE_HUMAN = """Task: {task}

Strategy from Architect:
{strategy}

Language: {language}

Write the implementation now."""

code_chain = (
    ChatPromptTemplate.from_messages([
        ("system", CODE_SYSTEM),
        ("human", CODE_HUMAN),
    ])
    | llm
    | StrOutputParser()
)

# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    """Remove ```lang ... ``` fences if the model adds them despite instructions."""
    return re.sub(r"^```[\w]*\n?|```$", "", raw.strip(), flags=re.MULTILINE).strip()


def _detect_language(task: str, context: dict) -> str:
    """Fallback language detection from task text."""
    lang = context.get("language", "")
    if lang:
        return lang
    task_lower = task.lower()
    if any(k in task_lower for k in ["yaml", "kubernetes", "k8s", "deployment"]):
        return "yaml"
    if any(k in task_lower for k in ["bash", "shell", "script"]):
        return "bash"
    if any(k in task_lower for k in ["javascript", "node", "react"]):
        return "javascript"
    return "python"


# ──────────────────────────────────────────────────────────────────
# Action handlers
# ──────────────────────────────────────────────────────────────────

async def _generate(payload: dict) -> dict:
    task = payload.get("task", "")
    strategy = payload.get("strategy", "No strategy provided.")
    context = payload.get("context", {})
    language = _detect_language(task, context)

    logger.info("Generating %s code for: %s", language, task[:80])
    t0 = time.perf_counter()

    try:
        raw_code = await code_chain.ainvoke({
            "task": task,
            "strategy": strategy,
            "language": language,
        })
        code = _strip_fences(raw_code)
        REQUESTS.labels("coder_agent", "generate", "success").inc()
        logger.info("Generated %d lines of %s code", len(code.splitlines()), language)
        return {
            "code": code,
            "language": language,
            "line_count": len(code.splitlines()),
        }
    except Exception:
        REQUESTS.labels("coder_agent", "generate", "error").inc()
        raise
    finally:
        DURATION.labels("coder_agent", "generate").observe(time.perf_counter() - t0)


# ──────────────────────────────────────────────────────────────────
# A2A route
# ──────────────────────────────────────────────────────────────────

app.add_api_route(
    "/a2a",
    create_a2a_handler("coder_agent", {"generate": _generate}),
    methods=["POST"],
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8003))
    uvicorn.run(app, host="0.0.0.0", port=port)
