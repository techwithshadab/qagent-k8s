#!/usr/bin/env bash
# scripts/setup_cluster.sh
# =========================
# One-shot bootstrap for the QAgent-K8s workshop cluster.
# Creates a kind cluster, builds all images, deploys all manifests,
# and verifies the system is ready.
#
# Estimated time: 5-8 minutes on a typical dev laptop.
#
# Prerequisites:
#   - Docker running
#   - kind, kubectl installed
#   - GEMINI_API_KEY environment variable set
#
# Usage:
#   GEMINI_API_KEY=<your-key> ./scripts/setup_cluster.sh
#   GEMINI_API_KEY=<your-key> ./scripts/setup_cluster.sh --cluster my-cluster

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-qagent-workshop}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --cluster) CLUSTER_NAME="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── Pre-flight checks ────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  QAgent-K8s Workshop Setup"
echo "  Cluster: $CLUSTER_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    echo "ERROR: GEMINI_API_KEY is not set."
    echo "  export GEMINI_API_KEY=your-key-here"
    exit 1
fi

for tool in docker kind kubectl; do
    if ! command -v "$tool" &>/dev/null; then
        echo "ERROR: '$tool' is not installed or not in PATH."
        exit 1
    fi
done

echo "  ✅  Prerequisites met"
echo ""

# ── Step 1: Create cluster ───────────────────────────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "  ℹ️   Cluster '${CLUSTER_NAME}' already exists — skipping create"
else
    echo "Step 1/5: Creating kind cluster '${CLUSTER_NAME}'..."
    kind create cluster --name "$CLUSTER_NAME" --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
EOF
    echo "  ✅  Cluster created"
fi

kubectl cluster-info --context "kind-${CLUSTER_NAME}" > /dev/null
echo ""

# ── Step 2: Build & load images ──────────────────────────────────
echo "Step 2/5: Building agent images..."
./scripts/build_and_load.sh --cluster "$CLUSTER_NAME"
echo ""

# ── Step 3: Apply base manifests ─────────────────────────────────
echo "Step 3/5: Deploying manifests..."
kubectl apply -f k8s/base/namespace.yaml

kubectl create secret generic qagent-secrets \
    --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
    -n qagent \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f k8s/base/config.yaml
kubectl apply -f k8s/base/rbac.yaml
kubectl apply -f k8s/base/agents.yaml
kubectl apply -f k8s/base/hpa.yaml
echo "  ✅  Base manifests applied"
echo ""

# ── Step 4: Wait for pods ────────────────────────────────────────
echo "Step 4/5: Waiting for all pods to be ready (up to 3 min)..."
kubectl wait --for=condition=ready pod \
    -l app.kubernetes.io/part-of=qagent-k8s \
    -n qagent \
    --timeout=180s || {
    echo ""
    echo "  ⚠️  Some pods not ready. Checking status:"
    kubectl get pods -n qagent
    echo ""
    echo "  Check logs: kubectl logs -l app=<agent> -n qagent"
    exit 1
}
echo "  ✅  All pods ready"
echo ""

# ── Step 5: Health check ─────────────────────────────────────────
echo "Step 5/5: Starting port-forwards and running health check..."
./scripts/port_forward_all.sh &
sleep 4

python scripts/health_check.py --wait --timeout 30 || {
    echo "  ⚠️  Health check failed — but pods are running."
    echo "      Port-forwards may need a moment. Try manually:"
    echo "      curl http://localhost:8000/healthz"
}

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🎉  QAgent-K8s Workshop Ready!"
echo ""
echo "  Test the full pipeline:"
echo "    curl -X POST http://localhost:8000/run \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -d '{\"user_request\": \"Write a hello world in Python\"}' \\"
echo "         | python -m json.tool"
echo ""
echo "  Lab guides:"
echo "    cat lab-guides/lab-01-mental-models.md"
echo "    cat lab-guides/lab-04-networking-security.md"
echo "    cat lab-guides/lab-06-capstone.md"
echo ""
echo "  Cleanup: kind delete cluster --name $CLUSTER_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
