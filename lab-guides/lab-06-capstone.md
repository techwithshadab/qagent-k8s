# Lab 06 — Capstone: Production Incident Simulation

**Module:** 6 — Capstone Scenario & Best Practices
**Duration:** ~25 minutes
**Level:** Intermediate — requires all prior modules to be deployed

---

## Scenario Brief

> **⚠️ ALERT — 14:32 UTC**
> PagerDuty: *QAgent production system degraded. User requests failing intermittently. On-call SRE unavailable. You are now the incident commander.*

Your system has **three concurrent faults** that were introduced by a bad deployment. No one told you what changed. You must:

1. Detect each fault
2. Diagnose the root cause
3. Apply the fix
4. Verify recovery

This is a realistic incident exercise. Work through it methodically — **don't guess, observe**.

---

## Incident Responder's Toolkit

```bash
# Essential commands — have these ready
kubectl get pods -n qagent                           # Pod status
kubectl describe pod <name> -n qagent               # Events + config
kubectl logs <pod> -n qagent --previous             # Logs from crashed container
kubectl top pods -n qagent                          # CPU/Memory usage
kubectl get events -n qagent --sort-by='.lastTimestamp'  # Recent events
kubectl exec -it <pod> -n qagent -- <command>       # Shell into container
```

### Observability stack (Grafana + Prometheus)

Open these before starting the incident — they provide real-time visibility:

```bash
# Port-forward if not already running
kubectl port-forward svc/kube-prom-grafana 3000:80 -n monitoring &
kubectl port-forward svc/kube-prom-kube-prometheus-prometheus 9090:9090 -n monitoring &
```

| Tool | URL | What to look for |
|------|-----|-----------------|
| Grafana | http://localhost:3000 (admin/admin) | "QAgent Multi-Agent Pipeline" dashboard — error rate spike, request drops, LLM fallbacks firing |
| Prometheus | http://localhost:9090/targets | All 7 `qagent/*` targets must be UP; down targets → pod crash or NetworkPolicy issue |
| `/metrics` direct | `curl http://localhost:8000/metrics \| grep qagent` | Raw counter values for any single agent |

---

## Part 1 — Apply the Broken Scenario (2 min)

```bash
# This overwrites parts of your working deployment with broken versions
kubectl apply -f k8s/capstone/broken-scenario.yaml

# Wait for rollout
kubectl rollout status deployment/orchestrator -n qagent
kubectl rollout status deployment/coder-agent -n qagent
```

Confirm things are broken:
```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a hello world in Python"}' \
  --max-time 15
```

You should see errors or very slow/no response.

---

## Part 2 — Incident Response Walkthrough (20 min)

### 🔴 Step 1: Triage — establish current system state

```bash
# 1. Check pod health first
kubectl get pods -n qagent -o wide

# 2. Check recent events (your first clue)
kubectl get events -n qagent --sort-by='.lastTimestamp' | tail -20

# 3. Check HPA status
kubectl get hpa -n qagent

# 4. Check Prometheus targets — any DOWN targets indicate a pod crash or network issue
#    Open http://localhost:9090/targets (requires port-forward above)

# 5. Check Grafana error rate panel — a spike pinpoints which agent is failing
#    Open http://localhost:3000 → Dashboards → "QAgent Multi-Agent Pipeline"
#    Look at: "Error Rate per Agent" and "Request Rate per Agent" panels
```

**What do you see?** Write down anomalies before proceeding.

---

### 🔴 Fault 1: OOMKilled Coder Agents

#### Detect

```bash
# Look for OOMKilled or CrashLoopBackOff in coder pods
kubectl get pods -n qagent | grep coder

# Check the resource limits on coder pods
kubectl describe deployment coder-agent -n qagent | grep -A5 "Limits\|Requests"

# Check events for memory-related messages
kubectl get events -n qagent | grep -i "OOM\|memory\|kill"
```

#### Diagnose

```bash
# Check what the container is using vs what it's allowed
kubectl top pods -n qagent -l app=coder-agent

# Read the broken configmap to understand what changed
kubectl get deployment coder-agent -n qagent -o jsonpath='{.spec.template.spec.containers[0].resources}' | python -m json.tool
```

**Root cause:** Memory limit set to `64Mi` — far too low for a Python LLM agent (typical: 256Mi+).

#### Fix

```bash
# Patch the memory limit directly
kubectl patch deployment coder-agent -n qagent \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": "512Mi"},
       {"op": "replace", "path": "/spec/template/spec/containers/0/resources/requests/memory", "value": "256Mi"}]'

# Watch pods recover
kubectl rollout status deployment/coder-agent -n qagent
kubectl get pods -n qagent -l app=coder-agent
```

#### Verify

```bash
kubectl top pods -n qagent -l app=coder-agent
# Should now show healthy memory headroom
```

---

### 🔴 Fault 2: Missing Readiness Probe on Orchestrator

#### Detect

```bash
# Check orchestrator deployment for readiness probe
kubectl describe deployment orchestrator -n qagent | grep -A10 "Readiness\|Liveness"

# Observe: requests fail during pod restarts because traffic is sent to unready pods
kubectl get endpoints orchestrator-svc -n qagent
```

#### Diagnose

The orchestrator has a liveness probe but **no readiness probe**. This means:
- Kubernetes sends traffic as soon as the container starts
- The FastAPI app takes ~5 seconds to initialize
- Requests hit the pod before it's ready → connection refused errors

Confirm by restarting the orchestrator and watching failures:
```bash
kubectl rollout restart deployment/orchestrator -n qagent

# Immediately hit the endpoint repeatedly
for i in {1..10}; do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz
  sleep 1
done
```

**You'll see 000 (refused) responses during startup** — this is the fault.

#### Fix

```bash
kubectl patch deployment orchestrator -n qagent \
  --type='json' \
  -p='[{
    "op": "add",
    "path": "/spec/template/spec/containers/0/readinessProbe",
    "value": {
      "httpGet": {"path": "/readyz", "port": 8000},
      "initialDelaySeconds": 5,
      "periodSeconds": 10
    }
  }]'

kubectl rollout status deployment/orchestrator -n qagent
```

#### Verify

```bash
# Restart again and watch — now traffic only flows when pod is ready
kubectl rollout restart deployment/orchestrator -n qagent

for i in {1..10}; do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz
  sleep 1
done
# Expected: 000 until pod is ready, then solid 200
```

---

### 🔴 Fault 3: Typo in Service URL (A2A Discovery Broken)

#### Detect

```bash
# Try a full pipeline request — it will fail at plan decomposition
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a hello world in Python"}' \
  --max-time 20

# Check orchestrator logs for the A2A error
kubectl logs -l app=orchestrator -n qagent | tail -30
```

You'll see: `httpx.ConnectError: http://plan-agnt-svc:8001` — note the typo.

#### Diagnose

```bash
# Read the broken ConfigMap
kubectl get configmap qagent-config-broken -n qagent -o yaml | grep PLAN

# Compare to correct config
kubectl get configmap qagent-config -n qagent -o yaml | grep PLAN
```

`plan-agnt-svc` vs `plan-agent-svc` — a one-character typo that completely breaks discovery.

#### Fix

```bash
# Fix the ConfigMap
kubectl patch configmap qagent-config-broken -n qagent \
  --type='json' \
  -p='[{
    "op": "replace",
    "path": "/data/PLAN_AGENT_SVC_URL",
    "value": "http://plan-agent-svc:8001"
  }]'

# Restart orchestrator to pick up the new config
kubectl rollout restart deployment/orchestrator -n qagent
kubectl rollout status deployment/orchestrator -n qagent
```

#### Verify

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"user_request": "Write a Python hello world"}' | python -m json.tool
```

**All three faults are resolved. Pipeline is healthy.**

---

## Part 3 — Post-Incident Review (3 min)

After fixing an incident, always run a blameless post-mortem:

### What broke

| Fault | Root Cause | Detection Clue |
|-------|-----------|----------------|
| OOMKilled coders | Memory limit 64Mi — insufficient for LLM process | `CrashLoopBackOff` + OOM events |
| No readiness probe | Deployment spec missing readinessProbe block | Intermittent 000 during rollout |
| Service URL typo | ConfigMap had `plan-agnt` not `plan-agent` | A2A ConnectError in orchestrator logs |

### What helped

- **Grafana dashboard** — error rate spike on `coder-agent` panel narrowed Fault 1 immediately
- **Prometheus targets** — DOWN target for orchestrator flagged the readiness probe issue (pod restarting repeatedly → scrape failing)
- Health probes (liveness caught crashes even without readiness)
- Structured JSON logs (error messages pointed directly to the bad URL)
- Kubernetes Events (showed OOMKilled clearly)
- `kubectl top` (confirmed memory pressure)

### What to design differently

1. **Always set readiness probes** — never deploy without them
2. **Use resource limits calibrated to measured usage** — run a load test first, then set limits at 2× the p95
3. **Validate ConfigMaps in CI** — a simple DNS lookup test catches typos before they reach production
4. **Use health check scripts in CI** — `kubectl rollout status --timeout=120s` fails the deployment if pods don't become ready

---

## Part 4 — Best Practices Summary Card

### Patterns ✅

| Pattern | Why |
|---------|-----|
| One agent per pod | Isolated restarts, independent scaling |
| Readiness + liveness probes | Traffic only to healthy pods |
| Resource requests AND limits | Predictable scheduling + blast radius control |
| NetworkPolicy default-deny | Zero-trust by default |
| A2A over ClusterIP services | K8s-native discovery, no service mesh needed |
| HPA on stateless agents | Scale compute with load |
| PodDisruptionBudget | Maintain availability during drains |
| Structured logging | Machine-readable for log aggregation |
| Prometheus metrics on every agent | Detect faults before users do; pinpoint which agent in a multi-hop pipeline failed |
| Grafana dashboard pre-built | On-call engineers need signal immediately — a blank dashboard during an incident costs minutes |

### Anti-Patterns ✗

| Anti-Pattern | Why It Fails |
|-------------|-------------|
| AI agent inside a monolith | One agent failure takes down everything |
| No resource limits | A noisy LLM call starves other pods |
| Hardcoded API URLs | Breaks with any service rename |
| Secrets in env vars via ConfigMap | Secrets should use `secretRef`, not `configMapRef` |
| Synchronous chain (A→B→C→D) | Each hop adds latency; parallelize where possible |
| No retry or timeout in A2A client | One slow agent blocks the entire pipeline |
| GPU for small models | Over-provisioned; use CPU for < 7B parameter models |

### When NOT to use AI agents

- When a simple LLM call solves the problem (no routing, no loops needed)
- When latency is <500ms SLA (agents add overhead)
- When cost per request must be minimized (every hop = another LLM call)
- When the task is fully deterministic (use a function, not an agent)

---

## Cleanup

```bash
# Full cluster cleanup after the workshop
kind delete cluster --name qagent-workshop
```

---

## Congratulations

You've deployed a production-grade multi-agent AI system on Kubernetes, secured it with zero-trust networking, diagnosed a realistic production incident, and applied best practices that translate directly to enterprise deployments.

The patterns you've used here — Orchestrator/Advisor/Coder/Reviewer topology, A2A protocol, zero-trust NetworkPolicy, HPA for stateless agents — are the same patterns used at scale in production AI systems.
