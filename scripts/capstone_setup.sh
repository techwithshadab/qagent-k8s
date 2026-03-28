#!/usr/bin/env bash
# scripts/capstone_setup.sh
# ==========================
# Sets up the Module 6 capstone production incident scenario.
# Applies the broken deployment manifests that introduce 3 faults
# for participants to diagnose and fix.
#
# Run this script DURING the capstone module introduction
# (after participants have a working system from Modules 1-5).
#
# Usage:
#   ./scripts/capstone_setup.sh          # Apply broken scenario
#   ./scripts/capstone_setup.sh restore  # Restore working deployment

set -euo pipefail

NAMESPACE="qagent"

restore() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Restoring working deployment..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    kubectl apply -f k8s/base/agents.yaml
    kubectl apply -f k8s/base/config.yaml

    kubectl rollout restart deployment/orchestrator -n "$NAMESPACE"
    kubectl rollout restart deployment/coder-agent -n "$NAMESPACE"

    echo ""
    echo "Waiting for rollouts to complete..."
    kubectl rollout status deployment/orchestrator -n "$NAMESPACE" --timeout=120s
    kubectl rollout status deployment/coder-agent -n "$NAMESPACE" --timeout=120s

    echo ""
    echo "✅  Restored! Verify with:"
    echo "    kubectl get pods -n qagent"
    echo "    curl -X POST http://localhost:8000/run -H 'Content-Type: application/json' \\"
    echo "         -d '{\"user_request\": \"Write hello world\"}'"
    exit 0
}

if [[ "${1:-}" == "restore" ]]; then
    restore
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ⚠️   Module 6 Capstone: Applying Broken Scenario"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  This introduces 3 realistic production faults:"
echo "  Fault 1: Memory limit too low on CoderAgent (OOMKilled)"
echo "  Fault 2: Missing readiness probe on Orchestrator"
echo "  Fault 3: Typo in ConfigMap service URL"
echo ""
echo "  Participants must diagnose and fix all three."
echo "  Restore with: ./scripts/capstone_setup.sh restore"
echo ""
read -r -p "Apply broken scenario? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Applying k8s/capstone/broken-scenario.yaml..."
kubectl apply -f k8s/capstone/broken-scenario.yaml

echo ""
echo "Waiting for rollouts..."
kubectl rollout status deployment/orchestrator -n "$NAMESPACE" --timeout=60s || true
kubectl rollout status deployment/coder-agent -n "$NAMESPACE" --timeout=60s || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Broken scenario applied!"
echo ""
echo "  Verify it's broken:"
echo "    kubectl get pods -n qagent"
echo "    curl -X POST http://localhost:8000/run \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -d '{\"user_request\": \"hello world\"}' --max-time 15"
echo ""
echo "  Now hand over to participants — lab-06-capstone.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
