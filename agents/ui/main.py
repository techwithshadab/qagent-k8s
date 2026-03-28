"""
UI Agent
========
Serves a chat-style web interface and proxies requests to the orchestrator.

Endpoints:
  GET  /      — serve the chat UI
  POST /run   — proxy to orchestrator /run, return JSON
  GET  /healthz, /readyz, /info
"""

import os
import sys
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from prometheus_client import Counter, Histogram
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from shared.utils import attach_health_routes, mount_metrics_endpoint, setup_logging

logger = setup_logging("ui")
app = FastAPI(title="QAgent UI", version="1.0.0")
attach_health_routes(app, "ui", version="1.0.0")
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

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_SVC_URL", "http://orchestrator-svc:8000")


class RunRequest(BaseModel):
    user_request: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/run")
async def proxy_run(req: RunRequest):
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{ORCHESTRATOR_URL}/run",
                json={"user_request": req.user_request},
            )
            resp.raise_for_status()
            result = resp.json()
        REQUESTS.labels("ui", "run", "success").inc()
        return result
    except Exception:
        REQUESTS.labels("ui", "run", "error").inc()
        raise
    finally:
        DURATION.labels("ui", "run").observe(time.perf_counter() - t0)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8005))
    uvicorn.run(app, host="0.0.0.0", port=port)
