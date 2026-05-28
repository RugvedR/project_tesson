"""
Tesson Phase 1 Proof Script — Milestone 1 Validation

TARGET: GoogleCloudPlatform/microservices-demo (highly complex, active repo)
SWEEP:  Last 50 closed (merged) Pull Requests

PASS CRITERIA:
  Metric 1 — Schema Integrity:  100% of outputs pass TessonSimplex validation
  Metric 2 — TERL Collapse:     redis-cart, RedisCartCache, redis_cart → same ID

Run:
  python scripts/run_phase1_proof.py

Requires:
  GITHUB_TOKEN and OPENAI_API_KEY in .env
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

# Add src/ to path — matches pyproject.toml package layout (where=["src"])
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from ingestion.graph import build_pipeline
from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store

logging.basicConfig(
    level=logging.WARNING,  # suppress debug noise during proof run
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tesson.proof")

# ─── Config ──────────────────────────────────────────────────────────────────

TARGET_REPO = "GoogleCloudPlatform/microservices-demo"
PR_COUNT = 50
PROOF_LEDGER = Path("./data/proof_ledger.jsonl")
PROOF_TERL = Path("./data/proof_terl.json")

# Aliases we expect to collapse to the same ID (Metric 2)
REDIS_ALIASES = ["redis-cart", "RedisCartCache", "redis_cart", "redis"]


def _fetch_prs(repo_name: str, count: int) -> list[dict]:
    """Fetch the last N closed+merged PRs from GitHub."""
    from github import Auth, Github

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN not set in .env")
        sys.exit(1)

    gh = Github(auth=Auth.Token(token))
    repo = gh.get_repo(repo_name)

    print(f"  Fetching last {count} merged PRs from {repo_name}...")
    prs = []
    for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if pr.merged_at is not None:
            prs.append({
                "number": pr.number,
                "title": pr.title,
                "merged_at": str(pr.merged_at),
            })
            if len(prs) >= count:
                break

    print(f"  ✓ Found {len(prs)} merged PRs")
    return prs


def _run_proof():
    print("\n" + "═" * 60)
    print("  TESSON PHASE 1 PROOF — MILESTONE 1 VALIDATION")
    print("═" * 60)

    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    print(f"\n  LLM Provider : {provider.upper()}")
    print(f"  Target Repo  : {TARGET_REPO}")
    print(f"  PR Count     : {PR_COUNT}")

    # ── Setup ────────────────────────────────────────────────────────
    print("\n[1/4] Initialising pipeline...")
    terl = EntityResolutionLedger(ledger_path=PROOF_TERL)
    from ingestion.graph import _build_llm
    llm = _build_llm()
    pipeline = build_pipeline(terl=terl, llm=llm)
    print(f"  ✓ Pipeline compiled ({provider.upper()} LLM ready)")

    # ── Fetch PRs ──────────────────────────────────────────────────────────
    print(f"\n[2/4] Fetching {PR_COUNT} PRs from {TARGET_REPO}...")
    prs = _fetch_prs(TARGET_REPO, PR_COUNT)

    # ── Run Pipeline ───────────────────────────────────────────────────────
    print(f"\n[3/4] Running {len(prs)} PRs through LangGraph pipeline...\n")
    print(f"  Rate limiting: 2s between calls (safe for Groq 30 RPM / Gemini 15 RPM)\n")

    results = []
    passed = 0
    failed = 0
    errors = []

    # Delay between calls to respect free tier rate limits
    # Groq: 30 RPM = 2s delay. Gemini: 5 RPM = 12s delay. OpenAI: 0.5s delay.
    if provider == "gemini":
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        if "lite" in model_name.lower():
            CALL_DELAY = 4.0   # 15 RPM = 4s delay
        else:
            CALL_DELAY = 12.0  # 5 RPM = 12s delay
    elif provider == "groq":
        CALL_DELAY = 2.0
    else:
        CALL_DELAY = 0.5

    for i, pr_info in enumerate(prs, start=1):
        pr_number = pr_info["number"]
        print(f"  [{i:2d}/{len(prs)}] PR #{pr_number}: {pr_info['title'][:50]!r}...", end=" ")

        t0 = time.time()
        try:
            initial_state = {
                "raw_payload": {},
                "repo": TARGET_REPO,
                "pr_number": pr_number,
                "diff_text": None,
                "llm_raw_output": None,
                "llm_error": None,
                "retries": 0,
                "resolved_simplex": None,
                "pipeline_error": None,
            }
            final_state = pipeline.invoke(initial_state)
            elapsed = time.time() - t0
            pipeline_error = final_state.get("pipeline_error")
            simplex = final_state.get("resolved_simplex")

            if pipeline_error:
                print(f"FAIL ✗ ({elapsed:.1f}s) — {pipeline_error}")
                failed += 1
                errors.append({"pr": pr_number, "error": pipeline_error})
            elif simplex is None:
                print(f"FAIL ✗ ({elapsed:.1f}s) — No simplex produced")
                failed += 1
                errors.append({"pr": pr_number, "error": "No simplex produced"})
            else:
                # ── Metric 1: Validate the simplex ─────────────────────────
                try:
                    TessonSimplex.model_validate(simplex.model_dump())
                    print(f"PASS ✓ ({elapsed:.1f}s) — simplex: {simplex.simplex_id[:8]}...")
                    passed += 1
                    results.append(simplex)
                    # Append to proof ledger
                    store.append(simplex, ledger_path=PROOF_LEDGER)
                except Exception as ve:
                    print(f"FAIL ✗ ({elapsed:.1f}s) — Pydantic: {ve}")
                    failed += 1
                    errors.append({"pr": pr_number, "error": str(ve)})

        except Exception as e:
            elapsed = time.time() - t0
            print(f"FAIL ✗ ({elapsed:.1f}s) — Exception: {e}")
            failed += 1
            errors.append({"pr": pr_number, "error": str(e)})

        # Rate limit guard — always sleep between LLM calls
        if i < len(prs):
            time.sleep(CALL_DELAY)

    # ── Metric 2: TERL Collapse ─────────────────────────────────────────────
    print("\n[4/4] Checking TERL collapse (Metric 2)...")
    redis_ids = {alias: terl.resolve_entity(alias) for alias in REDIS_ALIASES}
    unique_redis_ids = set(redis_ids.values())
    terl_collapse_passed = len(unique_redis_ids) == 1

    print("\n" + "─" * 60)
    print("  RESULTS")
    print("─" * 60)

    total = len(prs)
    pass_rate = (passed / total * 100) if total > 0 else 0

    print(f"\n  Metric 1 — Schema Integrity:")
    print(f"    PRs processed : {total}")
    print(f"    PASSED        : {passed}")
    print(f"    FAILED        : {failed}")
    print(f"    Pass rate     : {pass_rate:.1f}%")
    print(f"    Target        : 100.0%")

    print(f"\n  Metric 2 — TERL Entity Collapse:")
    for alias, resolved_id in redis_ids.items():
        print(f"    '{alias}' → '{resolved_id}'")
    print(f"    Unique IDs    : {len(unique_redis_ids)}")
    print(f"    Status        : {'✓ PASS (all collapsed to 1 ID)' if terl_collapse_passed else '✗ FAIL (multiple IDs found)'}")

    if errors:
        print(f"\n  Failures detail:")
        for e in errors[:10]:  # show first 10
            print(f"    PR #{e['pr']}: {e['error'][:100]}")

    print("\n" + "═" * 60)
    m1_pass = pass_rate == 100.0
    m2_pass = terl_collapse_passed

    if m1_pass and m2_pass:
        print("  🟢  PHASE 1 MILESTONE 1: PASSED")
        print("  Bedrock is ready for the sparse matrix math engine.")
    else:
        print("  🔴  PHASE 1 MILESTONE 1: FAILED")
        if not m1_pass:
            print(f"     ✗ Metric 1 failed ({pass_rate:.1f}% < 100%)")
        if not m2_pass:
            print(f"     ✗ Metric 2 failed ({len(unique_redis_ids)} unique IDs, expected 1)")
    print("═" * 60 + "\n")

    return 0 if (m1_pass and m2_pass) else 1


if __name__ == "__main__":
    sys.exit(_run_proof())
