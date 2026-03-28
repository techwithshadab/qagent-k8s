# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**qagent-k8s** is a workshop project: "Scaling AI on Kubernetes: From Demo to Production." It implements a multi-agent AI system where 6 FastAPI microservices collaborate via an Agent-to-Agent (A2A) HTTP protocol to decompose, advise on, generate, review code, and serve a chat UI.

All paths below are relative to the `qagent-k8s/` project folder.

---

## Common Commands

### Local Cluster Setup
```bash
kind create cluster --config kind-cluster.yaml
```
Cluster name: `qagent-workshop`. Requires Homebrew bash 5 for the build script.

### Build and Load Images
```bash
# Must use Homebrew bash 5 — macOS default bash 3 lacks associative arrays
/opt/homebrew/bin/bash scripts/build_and_load.sh
```

### Create Secrets (never via kubectl apply — do this manually)
```bash
# Primary + DeepSeek fallback keys
kubectl create secret generic qagent-secrets \
  --from-literal=VERTEX_API_KEY=<vertex-express-api-key> \
  --from-literal=DEEPSEEK_API_KEY=<ollama-cloud-key> \
  -n qagent

# GCP service account JSON (final LLM fallback)
kubectl create secret generic gcp-sa-key \
  --from-file=sa-key.json=datacouch-vertexai-454d3a1b2eb6.json \
  -n qagent
```

### Deploy to Kubernetes
```bash
kubectl apply -f k8s/base/        # Namespace, Deployments, Services, RBAC, HPA, ConfigMap
kubectl apply -f k8s/networking/  # NetworkPolicy (zero-trust)
kubectl apply -f k8s/security/    # ResourceQuota, LimitRange, PDB

# Patch CPU quota for local kind cluster (required — default 4 CPUs is not enough)
kubectl patch resourcequota qagent-quota -n qagent \
  --type merge -p '{"spec":{"hard":{"limits.cpu":"8","requests.cpu":"4"}}}'
```

### Access the UI
```bash
kubectl port-forward svc/ui-svc 8005:8005 -n qagent &
# Open http://localhost:8005
```

### Port Forwarding (all agents individually)
```bash
bash scripts/port_forward_all.sh
# Exposes ui:8005, orchestrator:8000, plan:8001, advisor:8002, coder:8003, reviewer:8004
```

### Deploy Prometheus + Grafana (kube-prometheus-stack)
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install kube-prom prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword=admin \
  --set grafana.sidecar.dashboards.enabled=true \
  --set grafana.sidecar.dashboards.label=grafana_dashboard \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false

# Apply observability manifests (ServiceMonitor, PrometheusRule, Grafana dashboard)
kubectl apply -f k8s/observability/
kubectl apply -f k8s/networking/network-policy.yaml  # includes allow-prometheus-scrape
```

### Access Grafana and Prometheus
```bash
kubectl port-forward svc/kube-prom-grafana 3000:80 -n monitoring &
kubectl port-forward svc/kube-prom-kube-prometheus-prometheus 9090:9090 -n monitoring &
# Grafana:    http://localhost:3000  (admin / admin)
# Prometheus: http://localhost:9090  → Status → Targets → look for qagent/*
```

### Kubernetes Dashboard
```bash
# Ensure kind context is active first
kubectl config use-context kind-qagent-workshop

kubectl proxy --port=8001 &
# Open: http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/
# Switch namespace to "qagent" in the top-left dropdown to see pods

# Get a login token (valid 8h)
kubectl create token dashboard-admin -n kubernetes-dashboard --duration=8h
```

### Health Checks
```bash
python scripts/health_check.py
```

### Load Testing (triggers HPA scaling on coder-agent)
```bash
python scripts/load_test.py
# Watch: kubectl get hpa -n qagent -w
```

### Capstone Incident Training
```bash
bash scripts/capstone_setup.sh
kubectl apply -f k8s/capstone/broken-scenario.yaml
```

---

## Architecture

### Agent Roles and Ports
| Agent | Port | Role |
|-------|------|------|
| ui | 8005 | Chat UI — Jinja2 + JS, proxies requests to orchestrator |
| orchestrator | 8000 | Central coordinator — receives user requests, fans out to agents |
| plan_agent | 8001 | Decomposes user request into 2-5 independent tasks |
| advisor_agent | 8002 | Produces coding strategy for each task |
| coder_agent | 8003 | Generates code from task + strategy (2 replicas, HPA 1–5) |
| reviewer_agent | 8004 | Validates code (approves if score ≥ 7, no critical security issues) |

### Request Flow
```
User → http://localhost:8005 (UI)
  → POST /run on Orchestrator (:8000)
    → PlanAgent: decompose request into tasks
    → For each task (in parallel via asyncio.gather):
        → AdvisorAgent: advise
        → CoderAgent: generate
        → ReviewerAgent: review
  → Return structured report to UI
```

### LLM Fallback Chain (shared/llm_client.py)
```
Tier 1 — VERTEX_API_KEY  → Gemini 2.5 Flash Lite  (generativelanguage.googleapis.com)
    ↓ ResourceExhausted (quota exhausted)
Tier 2 — DEEPSEEK_API_KEY → deepseek-v3.1:671b    (Ollama Cloud, ollama.com/api/chat)
    ↓ Any error
Tier 3 — GCP SA JSON      → Gemini 2.5 Flash Lite  (Vertex AI, aiplatform.googleapis.com)
```

- Uses LangChain's `with_fallbacks()` — returned object is a proper `Runnable`, works with `prompt | llm` chains.
- Ollama Cloud is called via `httpx` directly (no `ollama` package — avoids pydantic version conflict).
- SA credentials are mounted at `/app/secrets/sa-key.json` via the `gcp-sa-key` K8s secret.

### Shared Libraries (`shared/`)
- **llm_client.py**: 3-tier LLM with automatic fallback. `get_llm(temperature)` returns a `Runnable`. `chat(llm, system_prompt, user_msg)` does single-turn inference.
- **a2a_protocol.py**: A2A HTTP protocol — `A2ARequest`/`A2AResponse` models, `A2AClient` for inter-agent calls, `create_a2a_handler()` for FastAPI endpoint creation. Service URLs from env vars (e.g., `PLAN_AGENT_SVC_URL`).
- **utils.py**: `setup_logging()`, `attach_health_routes()`, and `mount_metrics_endpoint()` — every agent calls these at startup. `mount_metrics_endpoint(app)` mounts the Prometheus `/metrics` ASGI endpoint using `make_asgi_app()` from `prometheus_client`.

### Kubernetes Layout (`k8s/`)
- **base/**: Namespace, Deployments (6 agents), Services, RBAC, ConfigMap (`qagent-config`), HPA for coder-agent (1–5 replicas, 60% CPU target). **No Secret YAML** — secrets are created manually.
- **networking/**: Zero-trust NetworkPolicy — default deny all; orchestrator receives external traffic; UI can reach orchestrator; agents only accept from orchestrator. `allow-prometheus-scrape` policy explicitly permits ingress from the `monitoring` namespace on ports 8000–8005 so Prometheus can scrape `/metrics`.
- **security/**: ResourceQuota (patched to 8 CPU for local kind), LimitRange, PodDisruptionBudget.
- **observability/**: ServiceMonitor + PrometheusRule + `grafana-dashboard.yaml` (ConfigMap with pre-built 10-panel Grafana dashboard, auto-loaded by Grafana sidecar via `grafana_dashboard: "1"` label).
- **capstone/**: Intentionally broken manifests for incident response training (3 faults: OOMKill, missing readiness probe, ConfigMap typo).

### Configuration (ConfigMap `qagent-config`)
| Key | Value | Notes |
|-----|-------|-------|
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Used by tiers 1 and 3 |
| `DEEPSEEK_MODEL` | `deepseek-v3.1:671b` | Ollama Cloud model name |
| `DEEPSEEK_BASE_URL` | `https://ollama.com` | Ollama Cloud host |
| `GCP_SA_KEY_PATH` | `/app/secrets/sa-key.json` | Mounted from `gcp-sa-key` secret |
| `GOOGLE_CLOUD_PROJECT` | `datacouch-vertexai` | Vertex AI project |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex AI region |

### Secrets (created manually — never committed)
| Secret | Keys | Purpose |
|--------|------|---------|
| `qagent-secrets` | `VERTEX_API_KEY`, `DEEPSEEK_API_KEY` | LLM API keys |
| `gcp-sa-key` | `sa-key.json` | GCP service account JSON, mounted at `/app/secrets/` |

### Dockerfiles
All 6 agent Dockerfiles follow the same pattern:
- Base: `python:3.11-slim`
- Uses `uv` for fast pip installs (`ghcr.io/astral-sh/uv:latest`)
- Copies `shared/` and `agents/<name>/` into `/app/`
- Sets `PYTHONPATH=/app` so `shared/` is importable
- Runs as non-root user `appuser`

---

## Key Rules
- **Never commit** files matching `*secret*.yaml`, `.env*`, or any SA JSON files.
- **Always use `/opt/homebrew/bin/bash`** for `build_and_load.sh` on macOS (bash 5 required).
- **Never run `kubectl apply -f k8s/base/`** after secrets are created — it will NOT overwrite manually created secrets (Secret YAML has been removed from `config.yaml`).
- **LangChain braces in prompts**: JSON examples in system prompts must use `{{` and `}}` (doubled) to avoid LangChain treating them as template variables.
- **PYTHONPATH=/app** must be set in all Dockerfiles — agents `cd` into their subdirectory so `shared/` is only findable via absolute path.
- **`uvicorn.run(app, ...)`** — always pass the app **object**, never the string `"main:app"`. The string form causes uvicorn to re-import the module as a separate entry from `__main__`, which runs all module-level `prometheus_client` metric registrations twice → `ValueError: Duplicated timeseries in CollectorRegistry`.
- **ServiceMonitor label requirement**: The `ServiceMonitor` in `k8s/observability/monitoring.yaml` selects services with `app.kubernetes.io/part-of: qagent-k8s`. All 6 agent **Services** (not just Deployments) must carry this label, or Prometheus will find 0 targets.
- **Grafana sidecar restart**: If Grafana sidecar is stuck in `Loading incluster config...` loop after a failed helm install, run `kubectl rollout restart deployment/kube-prom-grafana -n monitoring`.
