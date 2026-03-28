FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy shared utilities (needed for attach_health_routes / setup_logging)
COPY shared/ ./shared/

# Copy UI agent code
COPY agents/ui/ ./agents/ui/

WORKDIR /app/agents/ui

ENV PYTHONPATH=/app

RUN adduser --disabled-password --no-create-home appuser
USER appuser

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8005/healthz')"

CMD ["python", "main.py"]
