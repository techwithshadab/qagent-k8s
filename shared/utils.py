"""
Shared utilities: structured logging, health checks, metrics helpers.
"""

import logging
import os
import time
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse


# ──────────────────────────────────────────────────────────────────
# Structured logging
# ──────────────────────────────────────────────────────────────────

def setup_logging(agent_name: str) -> logging.Logger:
    """Configure JSON-compatible structured logging."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        format=f"%(asctime)s  [{agent_name}]  %(levelname)s  %(message)s",
        level=getattr(logging, log_level, logging.INFO),
    )
    return logging.getLogger(agent_name)


# ──────────────────────────────────────────────────────────────────
# Health check routes — mount on every agent FastAPI app
# ──────────────────────────────────────────────────────────────────

def attach_health_routes(app: FastAPI, agent_name: str, version: str = "1.0.0") -> None:
    """Add /healthz and /readyz endpoints to any FastAPI app."""

    _start_time = time.time()

    @app.get("/healthz")
    async def liveness():
        """Kubernetes liveness probe — is the process alive?"""
        return {"status": "ok", "agent": agent_name}

    @app.get("/readyz")
    async def readiness():
        """Kubernetes readiness probe — is the agent ready for traffic?"""
        return {
            "status": "ready",
            "agent": agent_name,
            "version": version,
            "uptime_seconds": round(time.time() - _start_time, 1),
        }

    @app.get("/info")
    async def info():
        """Returns agent metadata — useful for debugging."""
        return {
            "agent": agent_name,
            "version": version,
            "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"),
            "log_level": os.environ.get("LOG_LEVEL", "INFO"),
        }


def mount_metrics_endpoint(app: FastAPI) -> None:
    """Mount Prometheus /metrics endpoint on the FastAPI app."""
    from prometheus_client import make_asgi_app
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)


__all__ = ["setup_logging", "attach_health_routes", "mount_metrics_endpoint"]
