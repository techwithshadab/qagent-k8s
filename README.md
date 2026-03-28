# QAgent-K8s Workshop Repository

> **Scaling AI on Kubernetes: From Demo to Production**
> A hands-on multi-agent AI system built with LangChain, Gemini, and Kubernetes.

---

## System Architecture

```
User → Browser (http://localhost:8005)
          │
          ▼
┌─────────────────────────────────────────────────────┐
│              UI Agent Pod (:8005)                   │
│   Chat interface — proxies requests to Orchestrator │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              Orchestrator Pod (:8000)               │
│   Coordinates all agents via A2A protocol (HTTP)    │
└──────┬──────────┬──────────┬──────────┬────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
  PlanAgent  AdvisorAgent CoderAgent ReviewerAgent
  (:8001)    (:8002)      (:8003×2)  (:8004)
  Planner    Strategist   Generator  Validator
```

Each agent is a **FastAPI** app in its own Kubernetes Pod, communicating via the **A2A protocol** (HTTP POST to `/a2a`).

### LLM Fallback Chain

Every agent uses a 3-tier automatic fallback — no code changes needed when a quota is hit:

```
1. Vertex API Key  →  Gemini 2.5 Flash Lite  (generativelanguage.googleapis.com)
        │ ResourceExhausted (quota)
        ▼
2. Ollama Cloud    →  deepseek-v3.1:671b      (ollama.com/api/chat)
        │ Any error
        ▼
3. GCP Service Account  →  Gemini 2.5 Flash Lite  (Vertex AI, paid — no limits)
```

---

## Repository Structure

```
qagent-k8s/
├── agents/
│   ├── orchestrator/      # Central coordinator
│   ├── plan_agent/        # Decomposes requests into tasks
│   ├── advisor_agent/     # Provides coding strategy
│   ├── coder_agent/       # Generates code
│   ├── reviewer_agent/    # Reviews and approves code
│   └── ui/                # Chat UI (Jinja2 + vanilla JS)
├── shared/
│   ├── llm_client.py      # LLM wrapper with 3-tier fallback + metrics
│   ├── a2a_protocol.py    # A2A protocol client + server helpers + metrics
│   └── utils.py           # Health checks, logging, Prometheus /metrics mount
├── k8s/
│   ├── base/              # Namespace, Deployments, Services, RBAC, HPA, ConfigMap
│   ├── networking/        # NetworkPolicy (zero-trust + Prometheus scrape allowlist)
│   ├── security/          # ResourceQuota, LimitRange, PodDisruptionBudget
│   ├── observability/     # ServiceMonitor, PrometheusRule, Grafana dashboard ConfigMap
│   └── capstone/          # Broken manifests for incident simulation
├── scripts/               # Helper scripts (build, load, health check, load test)
├── requirements.txt       # Shared Python dependencies for all agents
└── kind-cluster.yaml      # kind cluster config (3 nodes + port mappings)
```

---

## Prerequisites

- **Docker Desktop** (running)
- **kind** v0.20+ — `brew install kind`
- **kubectl** — `brew install kubectl`
- **helm** v3+ — `brew install helm`
- **Homebrew bash 5** — `brew install bash` (macOS ships bash 3 which lacks associative arrays)
- A **Vertex AI Express API key** — [aistudio.google.com](https://aistudio.google.com)
- An **Ollama Cloud API key** — [ollama.com/settings/keys](https://ollama.com/settings/keys)
- A **GCP Service Account JSON** with Vertex AI access (for final fallback)

---

## Quick Start

### 1. Create the kind cluster

```bash
kind create cluster --config kind-cluster.yaml
```

This creates a 3-node cluster named `qagent-workshop` with host port `30080` mapped for the UI NodePort.

### 2. Create secrets

> **Never commit real keys.** Create secrets manually — they are never stored in YAML files.

```bash
# Primary LLM key + DeepSeek fallback key
kubectl create secret generic qagent-secrets \
  --from-literal=VERTEX_API_KEY=<your-vertex-api-key> \
  --from-literal=DEEPSEEK_API_KEY=<your-ollama-cloud-key> \
  -n qagent

# GCP service account JSON (final fallback — paid Vertex AI)
kubectl create secret generic gcp-sa-key \
  --from-file=sa-key.json=datacouch-vertexai-454d3a1b2eb6.json \
  -n qagent
```

### 3. Build and load all images

Uses **uv** for fast installs inside Docker. Requires Homebrew bash 5:

```bash
/opt/homebrew/bin/bash scripts/build_and_load.sh
```

This builds 6 images (`orchestrator`, `plan-agent`, `advisor-agent`, `coder-agent`, `reviewer-agent`, `ui-agent`) and loads them into the kind cluster.

### 4. Apply manifests

```bash
kubectl apply -f k8s/base/
kubectl apply -f k8s/networking/
kubectl apply -f k8s/security/
```

> **Note:** `qagent-secrets` and `gcp-sa-key` are created manually (step 2) and are not in `k8s/base/`. Running `kubectl apply -f k8s/base/` will not overwrite them.

### 5. Patch CPU quota (required for local kind cluster)

```bash
kubectl patch resourcequota qagent-quota -n qagent \
  --type merge -p '{"spec":{"hard":{"limits.cpu":"8","requests.cpu":"4"}}}'
```

### 6. Deploy Prometheus + Grafana (kube-prometheus-stack)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install kube-prom prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.adminPassword=admin \
  --set grafana.sidecar.dashboards.enabled=true \
  --set grafana.sidecar.dashboards.label=grafana_dashboard \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false

# Apply the QAgent ServiceMonitor, PrometheusRules, and Grafana dashboard
kubectl apply -f k8s/observability/
```

Wait for the monitoring stack to be ready:

```bash
kubectl get pods -n monitoring -w
# Wait until all pods show READY
```

### 7. Verify all pods are running

```bash
kubectl get pods -n qagent
```

Expected output:
```
NAME                             READY   STATUS    RESTARTS   AGE
advisor-agent-xxx                1/1     Running   0          1m
coder-agent-xxx                  1/1     Running   0          1m
coder-agent-xxx                  1/1     Running   0          1m
orchestrator-xxx                 1/1     Running   0          1m
plan-agent-xxx                   1/1     Running   0          1m
reviewer-agent-xxx               1/1     Running   0          1m
ui-agent-xxx                     1/1     Running   0          1m
```

### 8. Open the UI and observability tools

```bash
# Chat UI
kubectl port-forward svc/ui-svc 8005:8005 -n qagent &

# Grafana dashboard
kubectl port-forward svc/kube-prom-grafana 3000:80 -n monitoring &

# Prometheus (optional — for raw metric queries)
kubectl port-forward svc/kube-prom-kube-prometheus-prometheus 9090:9090 -n monitoring &
```

| Tool | URL | Credentials |
|------|-----|-------------|
| Chat UI | http://localhost:8005 | — |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |

Type any coding request in the UI. The multi-agent pipeline will plan, advise, generate, and review code. Metrics appear in Grafana within 15 seconds.

---

## Observability & Grafana Dashboard

### Metrics exposed by every agent

Each agent exposes a `/metrics` endpoint (Prometheus format) at its service port:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `qagent_agent_requests_total` | Counter | `agent`, `action`, `status` | Requests processed |
| `qagent_agent_duration_seconds` | Histogram | `agent`, `action` | Request latency |
| `qagent_a2a_requests_total` | Counter | `from_agent`, `to_agent`, `action`, `status` | A2A calls between agents |
| `qagent_a2a_duration_seconds` | Histogram | `from_agent`, `to_agent`, `action` | A2A call latency |
| `qagent_llm_requests_total` | Counter | `tier` | LLM calls per tier (primary/deepseek) |
| `qagent_llm_duration_seconds` | Histogram | `tier` | LLM latency per tier |
| `qagent_llm_fallbacks_total` | Counter | `to_tier` | Times fallback tier was triggered |

### Grafana dashboard panels

The pre-built **QAgent Multi-Agent Pipeline** dashboard (loaded automatically from `k8s/observability/grafana-dashboard.yaml`) shows:

- Request rate per agent
- Error rate per agent
- P50/P99 latency per agent
- LLM tier usage (pie chart — primary vs fallback)
- LLM fallbacks fired (stat panel with threshold colouring)
- A2A call rate and latency by route
- Coder-agent replica count (HPA)
- Pod restart rate

### Navigate to the dashboard

1. Open http://localhost:3000
2. Login with admin / admin (or your configured password)
3. Click **Dashboards** → Browse → search **QAgent**

### Verify Prometheus is scraping qagent

```bash
# Check all 7 qagent targets are UP
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
d = json.load(sys.stdin)
qagent = [t for t in d['data']['activeTargets'] if 'qagent' in t.get('labels', {}).get('namespace', '')]
for t in qagent:
    print(t['labels']['service'], '-', t['health'])
"
```

Expected: all 7 targets (orchestrator, plan-agent, advisor-agent, coder-agent ×2, reviewer-agent, ui) show `up`.

---

## Kubernetes Dashboard (Control Panel UI)

The Kubernetes Dashboard gives you a visual overview of pods, deployments, services, logs, and resource usage.

### Install

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.7.0/aio/deploy/recommended.yaml
```

### Create an admin service account

```bash
kubectl create serviceaccount dashboard-admin -n kubernetes-dashboard

kubectl create clusterrolebinding dashboard-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=kubernetes-dashboard:dashboard-admin
```

### Get the login token

```bash
kubectl create token dashboard-admin -n kubernetes-dashboard
```

Copy the token — you'll paste it into the Dashboard login screen.

### Access the Dashboard

> **Important:** If you have multiple Kubernetes contexts (Docker Desktop, minikube, kind), make sure `kubectl` is pointing at the kind cluster first:
> ```bash
> kubectl config use-context kind-qagent-workshop
> ```

Use `kubectl proxy` — it routes through your active kubeconfig context (kind) directly, no certificate issues:

```bash
kubectl proxy --port=8001 &
```

Open:

**http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/**

Select **Token** on the login screen and paste the token from the previous step. Switch namespace to **qagent** in the top-left dropdown to see only the workshop pods.

### Useful Dashboard views

| What to check | Where to find it |
|---|---|
| Pod status & restarts | Workloads → Pods |
| Container logs (live) | Click a pod → Logs tab |
| CPU / memory usage | Workloads → Pods (graph columns) |
| ConfigMap values | Config and Storage → ConfigMaps |
| HPA scaling activity | Workloads → Horizontal Pod Autoscalers |
| Network policies | Network → Network Policies |

---

## Troubleshooting

### Port already in use

```bash
# Find what process is holding the port (e.g. 8005)
lsof -i :8005

# Kill it by PID
kill -9 <PID>

# Or kill all kubectl port-forwards at once
pkill -f "kubectl port-forward"

# Kill all local agent processes (if you ran agents locally during testing)
pkill -f "main.py"
```

### Port-forward drops / "Network error: Failed to fetch"

Port-forwards are not persistent — they die if the pod restarts or the terminal session ends.

```bash
# Restart all port-forwards at once
pkill -f "kubectl port-forward" 2>/dev/null
kubectl port-forward svc/ui-svc 8005:8005 -n qagent &
kubectl port-forward svc/kube-prom-grafana 3000:80 -n monitoring &
kubectl port-forward svc/kube-prom-kube-prometheus-prometheus 9090:9090 -n monitoring &
```

### Grafana dashboard not loading / "No data"

If metrics panels show "No data" in Grafana:

**1. Check Prometheus targets are UP:**
```bash
kubectl port-forward svc/kube-prom-kube-prometheus-prometheus 9090:9090 -n monitoring &
curl -s "http://localhost:9090/api/v1/targets" | python3 -c "
import sys, json
d = json.load(sys.stdin)
qagent = [t for t in d['data']['activeTargets'] if 'qagent' in t.get('labels',{}).get('namespace','')]
print(f'{len(qagent)} qagent targets')
for t in qagent: print(' ', t['labels']['service'], t['health'], t.get('lastError',''))
"
```

If targets are `down` with `context deadline exceeded`:
```bash
# The NetworkPolicy is blocking Prometheus — reapply networking
kubectl apply -f k8s/networking/
```

If 0 qagent targets found:
```bash
# Services are missing the required label — reapply base
kubectl apply -f k8s/base/
```

**2. Check Grafana sidecar loaded the dashboard:**
```bash
kubectl logs -n monitoring -l app.kubernetes.io/name=grafana -c grafana-sc-dashboard --tail=20 | grep -i "qagent\|error\|Initial sync"
```

If you see only `"Loading incluster config..."` in a loop (Grafana pod was started before sidecar was ready):
```bash
kubectl rollout restart deployment/kube-prom-grafana -n monitoring
# Then restart the port-forward:
pkill -f "kubectl port-forward svc/kube-prom-grafana"
kubectl port-forward svc/kube-prom-grafana 3000:80 -n monitoring &
```

**3. Send a test request to generate data:**
```bash
curl -s -X POST http://localhost:8005/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a hello world Python function"}' > /dev/null
# Metrics appear in Grafana within 15s
```

### Pod not starting / CrashLoopBackOff

```bash
# Check pod status and recent events
kubectl get pods -n qagent
kubectl describe pod <pod-name> -n qagent

# Check container logs for the crash reason
kubectl logs -n qagent <pod-name>
kubectl logs -n qagent <pod-name> --previous   # logs from the crashed container
```

### `address already in use` when recreating kind cluster

Local agent processes from earlier testing can hold ports 8000–8004, blocking kind from binding.

```bash
pkill -f "main.py"
kind delete cluster --name qagent-workshop
kind create cluster --config kind-cluster.yaml
```

### kubectl commands fail / `Unable to connect to the server`

```bash
kubectl config current-context
kubectl config use-context kind-qagent-workshop
kubectl get nodes
```

### Pods stuck in `Pending` — CPU quota exceeded

```bash
kubectl describe pod <pod-name> -n qagent | grep -A5 "Events"
kubectl patch resourcequota qagent-quota -n qagent \
  --type merge -p '{"spec":{"hard":{"limits.cpu":"8","requests.cpu":"4"}}}'
```

### Secret was overwritten by `kubectl apply`

```bash
kubectl delete secret qagent-secrets -n qagent
kubectl create secret generic qagent-secrets \
  --from-literal=VERTEX_API_KEY=<your-vertex-api-key> \
  --from-literal=DEEPSEEK_API_KEY=<your-ollama-cloud-key> \
  -n qagent
kubectl delete pods --all -n qagent
```

### Dashboard not showing kind cluster resources

```bash
kubectl config use-context kind-qagent-workshop
pkill -f "kubectl proxy"
kubectl proxy --port=8001 &
```

Then open: http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/

---

## Testing via curl

```bash
# Via UI proxy
curl -X POST http://localhost:8005/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a Python function that reads a CSV and returns summary statistics"}'

# Direct to orchestrator (requires port-forward on 8000)
kubectl port-forward svc/orchestrator-svc 8000:8000 -n qagent &
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a Python function that reads a CSV and returns summary statistics"}'
```

---

## Port Reference

| Service | Local Port | K8s Service | Notes |
|---------|-----------|-------------|-------|
| UI (chat interface) | 8005 | `ui-svc:8005` | Main entry point |
| Orchestrator | 8000 | `orchestrator-svc:8000` | Direct API access |
| Plan Agent | 8001 | `plan-agent-svc:8001` | A2A only |
| Advisor Agent | 8002 | `advisor-agent-svc:8002` | A2A only |
| Coder Agent | 8003 | `coder-agent-svc:8003` | A2A only, 2 replicas |
| Reviewer Agent | 8004 | `reviewer-agent-svc:8004` | A2A only |
| Grafana | 3000 | `kube-prom-grafana:80` (monitoring ns) | Dashboards |
| Prometheus | 9090 | `kube-prom-kube-prometheus-prometheus:9090` (monitoring ns) | Raw metrics |
| K8s Dashboard proxy | 8001 | via `kubectl proxy` | Control plane UI |

Every agent also exposes `/metrics` at its service port for Prometheus scraping.

---

## Health Checks

```bash
# Check all agents at once
python scripts/health_check.py

# Or check individually via kubectl
kubectl exec -n qagent deploy/orchestrator -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/healthz').read())"
```

Each agent exposes:
- `GET /healthz` — liveness (is the process alive?)
- `GET /readyz` — readiness (is the agent ready to serve?)
- `GET /info` — agent metadata
- `GET /metrics` — Prometheus metrics

---

## Load Testing (triggers HPA scaling on coder-agent)

```bash
python scripts/load_test.py
# Watch coder-agent scale from 2 → 5 replicas
kubectl get hpa -n qagent -w
```

Watch the Grafana dashboard while load testing — you'll see `qagent_agent_requests_total` and coder replica count rise in real time.

---

## Useful kubectl Commands

```bash
# Watch pods
kubectl get pods -n qagent -w

# Follow logs for a specific agent
kubectl logs -n qagent -l app=orchestrator -f

# Check LLM fallback tier being used
kubectl logs -n qagent -l app=plan-agent | grep -E "fallback|DeepSeek|quota|SA"

# Confirm secrets are loaded in a pod
kubectl exec -n qagent deploy/plan-agent -- env | grep -E "VERTEX|DEEPSEEK|GCP"

# Check SA key is mounted
kubectl exec -n qagent deploy/plan-agent -- ls /app/secrets/

# Verify metrics endpoint is working
kubectl port-forward svc/plan-agent-svc 8001:8001 -n qagent &
curl -sL http://localhost:8001/metrics/ | grep qagent

# Restart all pods (e.g. after config change)
kubectl delete pods --all -n qagent
```

---

## Rebuilding After Code Changes

```bash
# Rebuild all 6 images and reload into kind
/opt/homebrew/bin/bash scripts/build_and_load.sh

# Reapply manifests (picks up any YAML changes)
kubectl apply -f k8s/base/
kubectl apply -f k8s/networking/

# Restart pods to pull new images
kubectl delete pods --all -n qagent
```

---

## Capstone: Incident Response Simulation

```bash
bash scripts/capstone_setup.sh
kubectl apply -f k8s/capstone/broken-scenario.yaml
```

Three intentional faults are injected:
1. **OOMKill** — pod exceeds memory limit
2. **Missing readiness probe** — pod never becomes ready
3. **ConfigMap typo** — agent crashes at startup

Use Grafana and `kubectl` to detect and diagnose each fault. See [Lab 06](lab-guides/lab-06-capstone.md) for the full walkthrough.

---

## Lab Guides

| Module | Lab |
|--------|-----|
| Module 1 | [Mental Models & Reference Architecture](lab-guides/lab-01-mental-models.md) |
| Module 4 | [Networking, Security & Zero-Trust](lab-guides/lab-04-networking-security.md) |
| Module 6 | [Capstone: Production Incident Simulation](lab-guides/lab-06-capstone.md) |
