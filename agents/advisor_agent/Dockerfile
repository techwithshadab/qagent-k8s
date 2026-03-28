FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY shared/ ./shared/
COPY agents/advisor_agent/ ./agents/advisor_agent/

WORKDIR /app/agents/advisor_agent

ENV PYTHONPATH=/app

RUN adduser --disabled-password --no-create-home appuser
USER appuser

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/healthz')"

CMD ["python", "main.py"]
