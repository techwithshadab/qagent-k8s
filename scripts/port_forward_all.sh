#!/usr/bin/env bash
# scripts/port_forward_all.sh
# ===========================
# Port-forward all 5 QAgent services to localhost for local development
# and workshop hands-on sessions.
#
# Usage:
#   chmod +x scripts/port_forward_all.sh
#   ./scripts/port_forward_all.sh
#   ./scripts/port_forward_all.sh stop    # Kill all port-forwards
#
# After running, agents are accessible at:
#   Orchestrator  → http://localhost:8000
#   Plan Agent    → http://localhost:8001
#   Advisor Agent → http://localhost:8002
#   Coder Agent   → http://localhost:8003
#   Reviewer Agent→ http://localhost:8004

set -euo pipefail

NAMESPACE="qagent"
PID_FILE="/tmp/qagent-port-forwards.pid"

stop_all() {
    if [[ -f "$PID_FILE" ]]; then
        echo "Stopping all port-forwards..."
        while IFS= read -r pid; do
            kill "$pid" 2>/dev/null && echo "  Killed PID $pid" || true
        done < "$PID_FILE"
        rm -f "$PID_FILE"
        echo "Done."
    else
        echo "No active port-forwards found (no PID file at $PID_FILE)"
        # Also try pkill as a fallback
        pkill -f "kubectl port-forward" 2>/dev/null && echo "Killed orphaned kubectl port-forward processes" || true
    fi
    exit 0
}

# Handle stop command
if [[ "${1:-}" == "stop" ]]; then
    stop_all
fi

# Check namespace exists
if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
    echo "ERROR: Namespace '$NAMESPACE' does not exist."
    echo "  Run: kubectl apply -f k8s/base/namespace.yaml"
    exit 1
fi

echo "Starting port-forwards for all QAgent services..."
echo ""

declare -A SERVICES=(
    ["orchestrator-svc"]="8000"
    ["plan-agent-svc"]="8001"
    ["advisor-agent-svc"]="8002"
    ["coder-agent-svc"]="8003"
    ["reviewer-agent-svc"]="8004"
)

> "$PID_FILE"  # Clear PID file

for svc in "${!SERVICES[@]}"; do
    port="${SERVICES[$svc]}"
    kubectl port-forward "svc/$svc" "${port}:${port}" -n "$NAMESPACE" \
        > "/tmp/pf-${svc}.log" 2>&1 &
    pid=$!
    echo "$pid" >> "$PID_FILE"
    echo "  ✅  $svc → http://localhost:${port}  (PID $pid)"
    sleep 0.3  # Small delay to avoid race conditions
done

echo ""
echo "All port-forwards active. PIDs saved to $PID_FILE"
echo ""
echo "Quick health check:"
sleep 2  # Give port-forwards time to establish
for port in 8000 8001 8002 8003 8004; do
    status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/healthz" 2>/dev/null || echo "000")
    if [[ "$status" == "200" ]]; then
        echo "  ✅  localhost:${port}/healthz → $status"
    else
        echo "  ⚠️   localhost:${port}/healthz → $status (pod may still be starting)"
    fi
done

echo ""
echo "To stop all: ./scripts/port_forward_all.sh stop"
echo "To test:     curl -X POST http://localhost:8000/run -H 'Content-Type: application/json' \\"
echo "               -d '{\"user_request\": \"Write a hello world in Python\"}'"
