#!/usr/bin/env bash
# scripts/build_and_load.sh
# ==========================
# Build all agent Docker images and load them into a kind cluster.
# Run this from the repo root after `kind create cluster --name qagent-workshop`.
#
# Usage:
#   ./scripts/build_and_load.sh                        # Build all + load into kind
#   ./scripts/build_and_load.sh --cluster my-cluster   # Custom cluster name
#   ./scripts/build_and_load.sh --no-load              # Build only, skip kind load
#   ./scripts/build_and_load.sh --agent coder_agent    # Build one agent only

set -euo pipefail

CLUSTER_NAME="qagent-workshop"
NO_LOAD=false
SINGLE_AGENT=""

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --cluster)   CLUSTER_NAME="$2"; shift 2 ;;
        --no-load)   NO_LOAD=true; shift ;;
        --agent)     SINGLE_AGENT="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Agents and their image names
declare -A AGENTS=(
    ["orchestrator"]="qagent/orchestrator"
    ["plan_agent"]="qagent/plan-agent"
    ["advisor_agent"]="qagent/advisor-agent"
    ["coder_agent"]="qagent/coder-agent"
    ["reviewer_agent"]="qagent/reviewer-agent"
    ["ui"]="qagent/ui-agent"
)

build_agent() {
    local agent_dir="$1"
    local image_name="$2"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Building: ${image_name}:latest"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    docker build \
        --tag "${image_name}:latest" \
        --file "agents/${agent_dir}/Dockerfile" \
        --build-arg BUILDKIT_INLINE_CACHE=1 \
        .

    echo "  ✅  Built ${image_name}:latest"

    if [[ "$NO_LOAD" == "false" ]]; then
        echo "  Loading into kind cluster '${CLUSTER_NAME}'..."
        kind load docker-image "${image_name}:latest" --name "$CLUSTER_NAME"
        echo "  ✅  Loaded into kind"
    fi
}

echo "QAgent-K8s: Build & Load"
echo "  Cluster : $CLUSTER_NAME"
echo "  Load    : $([[ $NO_LOAD == true ]] && echo 'no' || echo 'yes')"
echo ""

# Verify we're in the repo root
if [[ ! -f "requirements.txt" ]] || [[ ! -d "agents" ]]; then
    echo "ERROR: Run this script from the repo root (qagent-k8s/)."
    exit 1
fi

if [[ -n "$SINGLE_AGENT" ]]; then
    img="${AGENTS[$SINGLE_AGENT]:-}"
    if [[ -z "$img" ]]; then
        echo "ERROR: Unknown agent '$SINGLE_AGENT'. Valid: ${!AGENTS[*]}"
        exit 1
    fi
    build_agent "$SINGLE_AGENT" "$img"
else
    for agent_dir in "${!AGENTS[@]}"; do
        build_agent "$agent_dir" "${AGENTS[$agent_dir]}"
    done
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  All images built successfully!"
echo ""
echo "  Next steps:"
echo "  1. kubectl apply -f k8s/base/"
echo "  2. kubectl apply -f k8s/networking/"
echo "  3. kubectl apply -f k8s/security/"
echo "  4. kubectl get pods -n qagent -w"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
