#!/usr/bin/env python3
"""
scripts/direct_llm_call.py
==========================
Used in Lab 01 (Step 10) to compare a single direct Gemini call
against the full multi-agent pipeline — so participants can feel
the overhead cost and understand what they get in return.

Usage:
    GEMINI_API_KEY=<your-key> python scripts/direct_llm_call.py
    GEMINI_API_KEY=<your-key> python scripts/direct_llm_call.py --prompt "Write a bash script to check disk usage"
"""

import argparse
import os
import time

DEFAULT_PROMPT = (
    "Write a bash script to monitor disk usage and send an alert if above 80%"
)


def main():
    parser = argparse.ArgumentParser(description="Time a direct Gemini LLM call")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send")
    parser.add_argument("--model", default="gemini-1.5-flash", help="Gemini model name")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GEMINI_API_KEY environment variable first.")
        raise SystemExit(1)

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage
    except ImportError:
        print("ERROR: langchain-google-genai not installed. Run: pip install langchain-google-genai")
        raise SystemExit(1)

    llm = ChatGoogleGenerativeAI(
        model=args.model,
        google_api_key=api_key,
        temperature=0.2,
    )

    print(f"\n{'='*60}")
    print(f"  Model  : {args.model}")
    print(f"  Prompt : {args.prompt[:80]}...")
    print(f"{'='*60}")

    start = time.perf_counter()
    response = llm.invoke([HumanMessage(content=args.prompt)])
    elapsed = time.perf_counter() - start

    print(f"\n--- Response ({len(response.content)} chars) ---")
    print(response.content[:500])
    if len(response.content) > 500:
        print(f"  ... [{len(response.content) - 500} more chars]")

    print(f"\n{'='*60}")
    print(f"  Direct Gemini call:    {elapsed:.2f}s")
    print(f"  Compare to pipeline:   run `time curl -X POST http://localhost:8000/run ...`")
    print(f"  Pipeline overhead:     ~3-5x longer — gives you plan + strategy + review")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
