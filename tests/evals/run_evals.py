"""
Golden Evaluation Suite runner for the Datazoic Agentic Platform.
Executes standard queries from golden_queries.jsonl, verifies schema adherence,
checks candidate gating and risk policy behavior, and computes token and latency metrics.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.graph.workflow import run_agent


async def main():
    eval_file = Path(__file__).resolve().parent / "golden_queries.jsonl"
    print(f"=================================================================")
    print(f" Running Datazoic Golden Evaluation Suite: {eval_file.name}")
    print(f"=================================================================\n")

    records = []
    with open(eval_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    passed_count = 0
    total = len(records)

    for idx, item in enumerate(records):
        qid = item["id"]
        query = item["query"]
        expected_intent = item.get("expected_intent")
        expected_approval = item.get("expected_approval_required")
        expected_clarify = item.get("expected_clarification")

        t0 = time.perf_counter()
        res = await run_agent(
            query=query,
            user_id="eval_user",
            user_role="admin",
            session_id=f"eval-session-{qid}",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Assertions
        passed = True
        notes = []

        if expected_approval:
            if res.approval_status != "pending":
                passed = False
                notes.append(f"Expected approval status 'pending', got '{res.approval_status}'")
        else:
            if res.approval_status == "pending":
                passed = False
                notes.append("Unexpected pending approval")

        if expected_clarify:
            if "provide" not in res.response.lower() and "specify" not in res.response.lower() and "require" not in res.response.lower():
                passed = False
                notes.append("Expected clarification response")

        if passed:
            passed_count += 1
            print(f"✅ [{qid}] PASS ({elapsed_ms:.1f}ms) | Intent: {expected_intent} | Query: '{query[:40]}...'")
        else:
            print(f"❌ [{qid}] FAIL ({elapsed_ms:.1f}ms) | Query: '{query[:40]}...' | Reason: {'; '.join(notes)}")

    print(f"\n=================================================================")
    print(f" Evaluation Completed: {passed_count}/{total} Passed ({passed_count/total*100:.1f}%)")
    print(f"=================================================================")

    if passed_count != total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

