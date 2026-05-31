"""
Unit tests for the TessonEngine runtime class.
"""

from __future__ import annotations

import time
from pathlib import Path
import pytest

from engine.engine import TessonEngine
from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store


@pytest.fixture
def tmp_ledger(tmp_path):
    return tmp_path / "test_ledger.jsonl"


@pytest.fixture
def tmp_terl(tmp_path):
    return tmp_path / "test_terl.json"


def _write_simplex(ledger_path: Path, nodes: list[str], pr: int | None = None) -> TessonSimplex:
    simplex = TessonSimplex(nodes=nodes, pr_number=pr, commit_sha=f"abcdef0000{pr}" if pr else None)
    store.append(simplex, ledger_path=ledger_path)
    return simplex


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_double_buffer_swap(tmp_ledger, tmp_terl):
    """Force recompile -> verify live matrix updates and background worker swaps."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")

    engine = TessonEngine(ledger_path=tmp_ledger, terl_path=tmp_terl)
    assert engine._live_matrix.num_simplices == 0

    # Write a simplex and force recompile
    _write_simplex(tmp_ledger, ["a_service", "b_service"], pr=1)
    engine.force_recompile()

    assert engine._live_matrix.num_simplices == 1


def test_query_before_compile(tmp_path):
    """query_rca() before any compile (e.g. non-existent files) -> returns empty list, no crash."""
    nonexistent_ledger = tmp_path / "does_not_exist_ledger.jsonl"
    nonexistent_terl = tmp_path / "does_not_exist_terl.json"

    engine = TessonEngine(ledger_path=nonexistent_ledger, terl_path=nonexistent_terl)
    # The matrix files don't exist, query should not crash
    results = engine.query_rca(["a_service"])
    assert results == []


def test_mixed_alert_routing(tmp_ledger, tmp_terl):
    """Alert with 1 canonical + 1 provisional entity -> both paths return results."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")

    # Canonical simplex A-B
    s1 = _write_simplex(tmp_ledger, ["a_service", "b_service"], pr=10)

    # Register provisional entity C
    prov_id = terl.resolve_entity("provisional_c", pr_number=20)
    # Write provisional simplex containing it
    s2 = _write_simplex(tmp_ledger, ["a_service", prov_id], pr=20)

    engine = TessonEngine(ledger_path=tmp_ledger, terl_path=tmp_terl)
    engine.force_recompile()

    # Query with mixed: a_service (canonical) and provisional_c (provisional)
    results = engine.query_rca(["a_service", prov_id])

    # We expect BOTH simplices in the results:
    # - s1 (PR 10) from matrix diffusion on a_service
    # - s2 (PR 20) from provisional fast path on provisional_c
    assert len(results) >= 2
    simplex_ids = [r.simplex_id for r in results]
    assert s1.simplex_id in simplex_ids
    assert s2.simplex_id in simplex_ids


def test_background_worker_periodic_recompile(tmp_ledger, tmp_terl):
    """Verify background worker thread periodically re-compiles matricial state."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("node_a", ["node_a"], "service")
    terl.register_entity("node_b", ["node_b"], "service")

    engine = TessonEngine(ledger_path=tmp_ledger, terl_path=tmp_terl, recompile_interval_seconds=0.5)
    assert engine._live_matrix.num_simplices == 0

    engine.start_background_worker()
    try:
        # Write a simplex in the background
        _write_simplex(tmp_ledger, ["node_a", "node_b"], pr=50)
        # Wait for worker thread to run compilation
        time.sleep(1.0)
        assert engine._live_matrix.num_simplices == 1
    finally:
        engine.stop_background_worker()
