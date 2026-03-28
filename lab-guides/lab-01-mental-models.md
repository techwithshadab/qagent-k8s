# Lab 01 — AI Workloads & Agents on Kubernetes: Mental Models

**Module:** 1 — AI Workloads & Agents on Kubernetes: Mental Models
**Duration:** ~25 minutes (demo + follow-along)
**Level:** Intermediate

---

## Learning Objectives

By the end of this lab you will be able to:
- Describe the structural difference between an LLM service and an AI agent
- Map the QAgent-K8s system to the Kubernetes primitives it uses
- Identify the agent execution loop and where infra stress occurs
- Run the full multi-agent system locally and observe its request flow

---

## Prerequisites

| Tool | Min Version | Check |
|------|-------------|-------|
| Docker | 24+ | `docker --version` |
| kind | 0.22+ | `kind version` |
| kubectl | 1.29+ | `kubectl version --client` |
| Python | 3.11+ | `python --version` |
| curl / httpie | any | `curl --version` |

---

## Mental Model Reference Card

Before touching a terminal, internalize this:

```
LLM Service                    AI Agent
─────────────────────          ──────────────────────────────────────
• Stateless HTTP endpoint      • Stateful execution loop
• One request → one response   • One request → N LLM calls + tool calls
• Predictable latency          • Unbounded latency (retries, loops)
• Easy to scale (replicas)     • Tricky to scale (shared state, ordering)
• Fails fast and clearly       • Fails silently mid-loop
```

The QAgent-K8s system makes this concrete:

```
Orchestrator ──► PlanAgent     (1 LLM call: decompose)
             ──► AdvisorAgent  (1 LLM call: strategize)   ← per task
             ──► CoderAgent    (1 LLM call: generate)      ← per task
             ──► ReviewerAgent (1 LLM call: validate)      ← per task
```

For a 3-task plan, a single user request triggers **12 LLM calls**.

---

## Part A — Cluster Setup (10 min)

### Step 1: Create your local cluster

```bash
kind create cluster --name qagent-workshop --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
EOF
```

Verify nodes are ready:
```bash
kubectl get nodes
# Expected: 3 nodes in Ready state
```

### Step 2: Create namespace and secrets

```bash
kubectl apply -f k8s/base/namespace.yaml

# LLM API keys — replace with your actual values
kubectl create secret generic qagent-secrets \
  --from-literal=VERTEX_API_KEY=<your-vertex-express-api-key> \
  --from-literal=DEEPSEEK_API_KEY=<your-ollama-cloud-key> \
  -n qagent

# GCP service account JSON (LLM tier-3 fallback)
kubectl create secret generic gcp-sa-key \
  --from-file=sa-key.json=<path-to-your-sa-key.json> \
  -n qagent

kubectl apply -f k8s/base/config.yaml
kubectl apply -f k8s/base/rbac.yaml
```

> **LLM tiers**: The system has a 3-tier fallback chain — Vertex Express API → DeepSeek via Ollama Cloud → Vertex AI via GCP service account. All three keys are required for the full fallback to work; the first two are the most commonly used.

### Step 3: Build and load all agent images

```bash
# Must use Homebrew bash 5 on macOS (default bash 3 lacks associative arrays)
/opt/homebrew/bin/bash scripts/build_and_load.sh
```

> **Tip:** If builds are slow, you can pull pre-built images from the workshop registry (provided by the facilitator).

### Step 4: Deploy the agents

```bash
kubectl apply -f k8s/base/agents.yaml
kubectl apply -f k8s/base/ui.yaml
kubectl apply -f k8s/base/hpa.yaml
```

Watch pods come up:
```bash
kubectl get pods -n qagent -w
# Wait until all pods show 1/1 READY
```

Expected output:
```
NAME                              READY   STATUS    RESTARTS
advisor-agent-xxx                 1/1     Running   0
coder-agent-xxx                   1/1     Running   0
coder-agent-yyy                   1/1     Running   0
orchestrator-xxx                  1/1     Running   0
plan-agent-xxx                    1/1     Running   0
reviewer-agent-xxx                1/1     Running   0
ui-agent-xxx                      1/1     Running   0
```

---

## Part B — Explore the Reference Architecture (8 min)

### Step 5: Inspect the agent topology

```bash
# See all services (A2A discovery points)
kubectl get services -n qagent

# Describe the orchestrator — note the ports and selectors
kubectl describe deployment orchestrator -n qagent

# View the ConfigMap that wires agents together
kubectl get configmap qagent-config -n qagent -o yaml
```

**Discussion question:** What happens if `PLAN_AGENT_SVC_URL` points to a wrong address?

### Step 6: Read a live agent's self-description

```bash
# Port-forward the orchestrator
kubectl port-forward svc/orchestrator-svc 8000:8000 -n qagent &

# Hit the /info endpoint
curl http://localhost:8000/info | python -m json.tool
```

Do the same for the plan agent:
```bash
kubectl port-forward svc/plan-agent-svc 8001:8001 -n qagent &
curl http://localhost:8001/info | python -m json.tool
```

### Step 7: Map the execution loop

Draw this diagram on paper (or your notes):

```
1. POST /run → Orchestrator
2. Orchestrator → PlanAgent /a2a {"action": "decompose"}
3. For each task (in parallel):
   a. Orchestrator → AdvisorAgent /a2a {"action": "advise"}
   b. Orchestrator → CoderAgent   /a2a {"action": "generate"}
   c. Orchestrator → ReviewerAgent /a2a {"action": "review"}
4. Orchestrator assembles final_report
5. Response returned to user
```

**Where are the infra stress points?**
- Step 3 runs in parallel — N concurrent Gemini API calls
- CoderAgent scales out (HPA) to handle parallel load
- If any agent call times out, the entire pipeline waits

---

## Part C — Send Your First Request (7 min)

### Step 8: Open port-forwards

```bash
# Open all agents at once
kubectl port-forward svc/ui-svc 8005:8005 -n qagent &
kubectl port-forward svc/orchestrator-svc 8000:8000 -n qagent &

# Open http://localhost:8005 in your browser for the chat UI
# Or send requests directly to the orchestrator via curl below
```

### Step 9: Run the multi-agent pipeline

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "user_request": "Write a Python function that parses a log file and counts error occurrences by severity level"
  }' | python -m json.tool
```

Observe the response structure:
- `plan` — what PlanAgent decomposed your request into
- `results` — per-task output with code + review
- `final_report` — human-readable Markdown summary

### Step 10: Watch agent logs during a request

Open a second terminal and stream logs:
```bash
# Watch orchestrator coordinate
kubectl logs -f -l app=orchestrator -n qagent

# Watch coder agents generate
kubectl logs -f -l app=coder-agent -n qagent
```

Re-run the curl from Step 9 and watch the log timestamps to see the A2A call chain.

### Step 11: Observe the execution loop timing

```bash
# Time a request end-to-end
time curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a bash script to monitor disk usage and send an alert if above 80%"}' \
  > /dev/null
```

Note the elapsed time. Now compare with a direct LLM call (no agents):
```python
# scripts/direct_llm_call.py
import time, os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", google_api_key=os.environ["VERTEX_API_KEY"])
start = time.time()
response = llm.invoke([HumanMessage(content="Write a bash script to monitor disk usage and send an alert if above 80%")])
print(f"Direct call: {time.time()-start:.2f}s")
```

```bash
VERTEX_API_KEY=<your-key> python scripts/direct_llm_call.py
```

**Discussion:** The agent pipeline takes longer. Why? What do you get in return?

### Step 12: Inspect the Prometheus metrics endpoint

Each agent exposes a `/metrics` endpoint for Prometheus scraping:

```bash
# Check the orchestrator's live metrics
kubectl port-forward svc/orchestrator-svc 8000:8000 -n qagent &
curl http://localhost:8000/metrics | grep qagent
```

You'll see counters like `qagent_agent_requests_total`, `qagent_a2a_requests_total`, and `qagent_llm_requests_total`. These increment with every request — run the pipeline again and watch the counters rise.

```bash
# Send a request, then check metrics again
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a Python hello world"}' > /dev/null

curl -s http://localhost:8000/metrics | grep 'qagent_agent_requests_total{.*status="success"'
```

---

## Cleanup

```bash
# Kill port-forwards
pkill -f "kubectl port-forward"

# The cluster persists for later modules — do NOT delete it yet
```

---

## Key Takeaways

1. **AI agents ≠ LLM services**: Agents have loops, state, and unpredictable execution depth.
2. **Each agent pod = one responsibility**: Separation of concerns makes debugging possible.
3. **A2A over HTTP**: Simple, observable, and K8s-native. Services = discovery.
4. **Parallel tasks amplify infrastructure pressure**: 3 tasks = 3× concurrent API calls.
5. **Kubernetes gives you the building blocks**: Deployments, Services, HPA, and probes are sufficient to run production multi-agent systems.

---

## Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Pod stuck in `Pending` | No worker node capacity | Check `kubectl describe pod <name> -n qagent` |
| Pod `CrashLoopBackOff` | Missing secret or API key | Verify: `kubectl get secret qagent-secrets -n qagent` |
| `ValueError: Duplicated timeseries` | `uvicorn.run("main:app", ...)` double-import | Ensure all agents use `uvicorn.run(app, ...)` (object, not string) |
| `curl: Connection refused` | Port-forward not running | Re-run the port-forward command |
| Request times out | Vertex API quota exceeded | Wait 60 seconds — LLM fallback chain will retry via DeepSeek |
| Image pull error | Image not loaded into kind | Re-run `/opt/homebrew/bin/bash scripts/build_and_load.sh` |
| `/metrics` returns 404 | `mount_metrics_endpoint(app)` not called | Check agent `main.py` calls `mount_metrics_endpoint(app)` after `attach_health_routes` |
