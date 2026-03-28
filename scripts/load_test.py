#!/usr/bin/env python3
"""
scripts/load_test.py
====================
Send concurrent requests to the QAgent orchestrator to trigger
the HPA on the CoderAgent and demonstrate scaling behaviour.

Used in Module 3 (Sandeep) and Module 6 Capstone to show:
  - CoderAgent HPA scaling from 1 → N replicas
  - Request latency under load
  - Pod scheduling events

Usage:
    # Default: 20 requests, 5 concurrent
    python scripts/load_test.py

    # Custom: 50 requests, 10 concurrent, custom endpoint
    python scripts/load_test.py --requests 50 --concurrency 10 --url http://localhost:8000

    # Watch HPA in another terminal while running:
    #   kubectl get hpa -n qagent -w
    #   kubectl get pods -n qagent -w
"""

import argparse
import asyncio
import statistics
import time
from typing import List, Tuple

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    raise SystemExit(1)

SAMPLE_REQUESTS = [
    "Write a Python function to parse a JSON file and extract all email addresses",
    "Write a bash script to check disk usage on all mount points and alert if above 80%",
    "Write a Python class to implement an LRU cache with a configurable max size",
    "Write a Go function to compute a SHA-256 hash of a file",
    "Write a Python script to read a CSV file and compute summary statistics per column",
    "Write a bash function to retry a command up to N times with exponential backoff",
    "Write a Python decorator that measures and logs function execution time",
    "Write a YAML Kubernetes ConfigMap for application configuration",
    "Write a Python function to validate an email address using regex",
    "Write a bash script to rotate log files older than 7 days",
]


async def send_request(
    client: httpx.AsyncClient,
    url: str,
    prompt: str,
    req_num: int,
) -> Tuple[int, float, bool]:
    """Send one request, return (req_num, latency_seconds, success)."""
    payload = {"user_request": prompt}
    start = time.perf_counter()
    try:
        response = await client.post(f"{url}/run", json=payload, timeout=90.0)
        latency = time.perf_counter() - start
        success = response.status_code == 200
        status = response.status_code
    except Exception as exc:
        latency = time.perf_counter() - start
        success = False
        status = 0
        print(f"  [req {req_num:03d}] ERROR after {latency:.1f}s: {exc}")
        return req_num, latency, False

    icon = "✅" if success else "❌"
    print(f"  [req {req_num:03d}] {icon}  HTTP {status}  {latency:.2f}s")
    return req_num, latency, success


async def run_load_test(url: str, total_requests: int, concurrency: int):
    """Run the load test with controlled concurrency."""
    print(f"\n{'='*60}")
    print(f"  QAgent-K8s Load Test")
    print(f"  URL         : {url}")
    print(f"  Requests    : {total_requests}")
    print(f"  Concurrency : {concurrency}")
    print(f"{'='*60}")
    print(f"\nWatch scaling in another terminal:")
    print(f"  kubectl get hpa -n qagent -w")
    print(f"  kubectl get pods -n qagent -w")
    print(f"\nStarting in 3 seconds...")
    await asyncio.sleep(3)
    print()

    # Build list of prompts (cycle through samples)
    prompts = [SAMPLE_REQUESTS[i % len(SAMPLE_REQUESTS)] for i in range(total_requests)]

    semaphore = asyncio.Semaphore(concurrency)
    results: List[Tuple[int, float, bool]] = []
    start_time = time.perf_counter()

    async def bounded_request(client, prompt, req_num):
        async with semaphore:
            result = await send_request(client, url, prompt, req_num)
            results.append(result)

    async with httpx.AsyncClient() as client:
        tasks = [
            bounded_request(client, prompt, i + 1)
            for i, prompt in enumerate(prompts)
        ]
        await asyncio.gather(*tasks)

    total_time = time.perf_counter() - start_time

    # Stats
    latencies = [r[1] for r in results]
    successes = sum(1 for r in results if r[2])
    failures = total_requests - successes

    print(f"\n{'='*60}")
    print(f"  Results")
    print(f"{'='*60}")
    print(f"  Total requests : {total_requests}")
    print(f"  Succeeded      : {successes} ✅")
    print(f"  Failed         : {failures} ❌")
    print(f"  Total time     : {total_time:.1f}s")
    print(f"  Throughput     : {total_requests / total_time:.2f} req/s")
    print(f"")
    print(f"  Latency (seconds):")
    print(f"    min    : {min(latencies):.2f}s")
    print(f"    median : {statistics.median(latencies):.2f}s")
    print(f"    p95    : {sorted(latencies)[int(len(latencies)*0.95)]:.2f}s")
    print(f"    max    : {max(latencies):.2f}s")
    print(f"{'='*60}")
    print()
    print("Check if CoderAgent scaled:")
    print("  kubectl get pods -n qagent -l app=coder-agent")
    print("  kubectl get hpa coder-agent-hpa -n qagent")


def main():
    parser = argparse.ArgumentParser(description="QAgent-K8s load test")
    parser.add_argument("--url", default="http://localhost:8000", help="Orchestrator base URL")
    parser.add_argument("--requests", type=int, default=20, help="Total number of requests")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent requests")
    args = parser.parse_args()

    asyncio.run(run_load_test(args.url, args.requests, args.concurrency))


if __name__ == "__main__":
    main()
