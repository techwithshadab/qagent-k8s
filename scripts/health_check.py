#!/usr/bin/env python3
"""
scripts/health_check.py
=======================
Check health and readiness of all QAgent pods.
Useful at the start of each workshop module to confirm the system
is running before participants begin hands-on tasks.

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --namespace qagent
    python scripts/health_check.py --wait   # Block until all agents are healthy
"""

import argparse
import sys
import time
import urllib.request
import urllib.error

AGENTS = {
    "orchestrator":   "http://localhost:8000",
    "plan-agent":     "http://localhost:8001",
    "advisor-agent":  "http://localhost:8002",
    "coder-agent":    "http://localhost:8003",
    "reviewer-agent": "http://localhost:8004",
}


def check_agent(name: str, base_url: str) -> dict:
    """Check /healthz and /readyz for one agent."""
    result = {"name": name, "url": base_url, "healthy": False, "ready": False, "info": {}}

    for endpoint in ("healthz", "readyz"):
        try:
            resp = urllib.request.urlopen(f"{base_url}/{endpoint}", timeout=3)
            if endpoint == "healthz":
                result["healthy"] = resp.status == 200
            elif endpoint == "readyz":
                result["ready"] = resp.status == 200
                import json
                result["info"] = json.loads(resp.read())
        except Exception:
            pass  # Not healthy/ready

    return result


def print_results(results: list) -> bool:
    """Print formatted results. Returns True if all healthy."""
    all_ok = True
    print(f"\n{'Agent':<20} {'Healthy':<10} {'Ready':<10} {'Uptime':<12} {'Model'}")
    print(f"{'─'*20} {'─'*10} {'─'*10} {'─'*12} {'─'*20}")

    for r in results:
        h = "✅ yes" if r["healthy"] else "❌ no "
        rd = "✅ yes" if r["ready"] else "❌ no "
        uptime = r["info"].get("uptime_seconds", "?")
        model = r["info"].get("gemini_model", "?")
        uptime_str = f"{uptime:.0f}s" if isinstance(uptime, (int, float)) else str(uptime)
        print(f"{r['name']:<20} {h:<10} {rd:<10} {uptime_str:<12} {model}")
        if not (r["healthy"] and r["ready"]):
            all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Check QAgent health")
    parser.add_argument("--wait", action="store_true", help="Wait until all agents are healthy")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout seconds for --wait")
    args = parser.parse_args()

    print("QAgent-K8s Health Check")
    print("Make sure port-forwards are running: ./scripts/port_forward_all.sh")

    if args.wait:
        print(f"\nWaiting up to {args.timeout}s for all agents to become healthy...")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            results = [check_agent(n, u) for n, u in AGENTS.items()]
            all_ok = print_results(results)
            if all_ok:
                print("\n✅  All agents healthy and ready!")
                sys.exit(0)
            remaining = int(deadline - time.time())
            print(f"\nRetrying in 5s... ({remaining}s remaining)")
            time.sleep(5)
        print("\n❌  Timed out waiting for all agents.")
        sys.exit(1)
    else:
        results = [check_agent(n, u) for n, u in AGENTS.items()]
        all_ok = print_results(results)
        print()
        if all_ok:
            print("✅  All agents healthy and ready!")
            sys.exit(0)
        else:
            print("❌  Some agents are not ready. Check:")
            print("    kubectl get pods -n qagent")
            print("    kubectl logs -l app=<agent> -n qagent")
            sys.exit(1)


if __name__ == "__main__":
    main()
