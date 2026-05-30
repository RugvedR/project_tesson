"""
Phase 2 Step 1 — Concurrency Safety Tests

Tests that validate the cross-platform file locking implementation:
  1. Concurrent ledger appends do not interleave data
  2. Concurrent TERL persist operations produce valid JSON
  3. Lock timeout raises filelock.Timeout cleanly

These tests use ThreadPoolExecutor to simulate concurrent pipeline runs.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
from filelock import FileLock, Timeout as FileLockTimeout

from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_ledger(tmp_path):
    """Return a path to a temporary ledger file."""
    return tmp_path / "test_ledger.jsonl"


@pytest.fixture
def tmp_terl(tmp_path):
    """Return a path to a temporary TERL file."""
    return tmp_path / "test_terl.json"


def _make_simplex(pr_number: int) -> TessonSimplex:
    """Create a minimal valid simplex with a unique PR number."""
    return TessonSimplex(
        nodes=["cart_service", "redis_cache"],
        pr_number=pr_number,
        metadata={"test_id": str(pr_number)},
    )


# ─── Test 1: Concurrent Ledger Appends ───────────────────────────────────────

class TestConcurrentLedgerAppends:
    """10 threads each append a simplex. Verify all 10 are present
    and no data is interleaved (each line is valid JSON).
    """

    def test_concurrent_ledger_appends(self, tmp_ledger):
        num_threads = 10
        simplices = [_make_simplex(pr_number=i + 1) for i in range(num_threads)]

        def _append(simplex: TessonSimplex):
            store.append(simplex, ledger_path=tmp_ledger)

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_append, s) for s in simplices]
            for f in as_completed(futures):
                f.result()  # raise any exceptions

        # Verify all entries are present
        lines = tmp_ledger.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == num_threads, (
            f"Expected {num_threads} lines, got {len(lines)}"
        )

        # Verify each line is valid JSON (no interleaving)
        parsed_pr_numbers = set()
        for i, line in enumerate(lines, start=1):
            try:
                data = json.loads(line)
                parsed_pr_numbers.add(data["pr_number"])
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i} is not valid JSON (data interleaved?): {e}")

        # Verify all PR numbers are present (no overwrites)
        expected_prs = {i + 1 for i in range(num_threads)}
        assert parsed_pr_numbers == expected_prs, (
            f"Missing PRs: {expected_prs - parsed_pr_numbers}"
        )


# ─── Test 2: Concurrent TERL Persist ─────────────────────────────────────────

class TestConcurrentTERLPersist:
    """Two threads both resolve novel entities. Both must be persisted
    in valid JSON without corruption.
    """

    def test_concurrent_terl_persist(self, tmp_terl):
        terl = EntityResolutionLedger(ledger_path=tmp_terl)

        def _resolve(entity_name: str, pr: int):
            return terl.resolve_entity(entity_name, pr_number=pr)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_resolve, "zebra_analytics_engine", 201)
            f2 = pool.submit(_resolve, "quantum_scheduler_daemon", 202)
            id1 = f1.result()
            id2 = f2.result()

        # Both entities must have been registered
        assert id1 != id2, "Two distinct entities should get different IDs"

        # The persisted file must be valid JSON
        raw = tmp_terl.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            pytest.fail(f"TERL file is corrupted JSON after concurrent writes: {e}")

        # Both entities must be in the persisted data
        assert id1 in data, f"Entity '{id1}' not found in persisted TERL"
        assert id2 in data, f"Entity '{id2}' not found in persisted TERL"


# ─── Test 3: Lock Timeout Raises ─────────────────────────────────────────────

class TestLockTimeout:
    """Manually hold a lock, attempt a write with a short timeout,
    verify filelock.Timeout is raised.
    """

    def test_lock_timeout_raises(self, tmp_ledger):
        lock_path = str(tmp_ledger) + ".lock"
        blocker = FileLock(lock_path, timeout=0)

        # Hold the lock
        blocker.acquire()
        try:
            # Create a simplex and try to append with a very short timeout
            simplex = _make_simplex(pr_number=999)

            # Temporarily patch the store's lock timeout to 1 second
            repo = store.FileLedgerRepository(tmp_ledger)
            repo._lock = FileLock(lock_path, timeout=1)

            with pytest.raises(FileLockTimeout):
                repo.append_simplex(simplex)
        finally:
            blocker.release()
