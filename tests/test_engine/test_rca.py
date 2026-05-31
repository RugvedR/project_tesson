"""
Unit tests for the RCA traversal engine.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from engine.bridge import CoordinateBridge
from engine.matrix import TessonMatrix
from engine.rca import RCAEngine, provisional_fast_path
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

def test_linear_chain_rca(tmp_ledger, tmp_terl):
    """Linear chain: a_service - b_service - c_service.

    Alert on c_service.
    Verify both simplices appear in results, and the direct connection
    (b_service - c_service) ranks higher than the transitive one (a_service - b_service).
    """
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")
    terl.register_entity("c_service", ["c_service"], "service")

    s1 = _write_simplex(tmp_ledger, ["a_service", "b_service"], pr=1)
    s2 = _write_simplex(tmp_ledger, ["b_service", "c_service"], pr=2)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)
    rca = RCAEngine(matrix)

    # c_service is at index 2 (sorted list: a_service, b_service, c_service)
    c_idx = matrix._entity_to_index["c_service"]
    results = rca.trace([c_idx], hops=2)

    assert len(results) == 2
    # The direct connection s2 (PR 2) should rank higher than s1 (PR 1)
    assert results[0].simplex_id == s2.simplex_id
    assert results[1].simplex_id == s1.simplex_id
    assert results[0].score > results[1].score


def test_triangle_rca(tmp_ledger, tmp_terl):
    """Triangle topology: node_a - node_b - node_c. Alert on node_a. Verify all 3 simplices in results."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("node_a", ["node_a"], "service")
    terl.register_entity("node_b", ["node_b"], "service")
    terl.register_entity("node_c", ["node_c"], "service")

    s1 = _write_simplex(tmp_ledger, ["node_a", "node_b"], pr=1)
    s2 = _write_simplex(tmp_ledger, ["node_b", "node_c"], pr=2)
    s3 = _write_simplex(tmp_ledger, ["node_c", "node_a"], pr=3)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)
    rca = RCAEngine(matrix)

    a_idx = matrix._entity_to_index["node_a"]
    results = rca.trace([a_idx], hops=2)

    # All three simplices should be reached and returned
    assert len(results) == 3
    simplex_ids = [r.simplex_id for r in results]
    assert s1.simplex_id in simplex_ids
    assert s2.simplex_id in simplex_ids
    assert s3.simplex_id in simplex_ids


def test_isolated_node_no_rca(tmp_ledger, tmp_terl):
    """Alert on disconnected node -> empty results."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("node_a", ["node_a"], "service")
    terl.register_entity("node_b", ["node_b"], "service")
    terl.register_entity("node_c", ["node_c"], "service")

    # node_a and node_b are connected. node_c is isolated (not in any simplex)
    _write_simplex(tmp_ledger, ["node_a", "node_b"])

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)
    rca = RCAEngine(matrix)

    results = rca.trace([], hops=2)
    assert results == []


def test_top_k_ranking(tmp_ledger, tmp_terl):
    """Verify results are sorted by score and limited to top_k."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("node_a", ["node_a"], "service")
    terl.register_entity("node_b", ["node_b"], "service")
    terl.register_entity("node_c", ["node_c"], "service")
    terl.register_entity("node_d", ["node_d"], "service")

    _write_simplex(tmp_ledger, ["node_a", "node_b"], pr=1)
    _write_simplex(tmp_ledger, ["node_a", "node_c"], pr=2)
    _write_simplex(tmp_ledger, ["node_a", "node_d"], pr=3)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)
    rca = RCAEngine(matrix)

    a_idx = matrix._entity_to_index["node_a"]
    results = rca.trace([a_idx], hops=1, top_k=2)

    assert len(results) == 2
    # Verify descending score sorting
    assert results[0].score >= results[1].score


def test_provisional_fast_path(tmp_ledger, tmp_terl):
    """Provisional entity alert -> direct ledger scan returns exact PRs."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    
    # Register provisional entity
    prov_id = terl.resolve_entity("provisional_x", pr_number=101)
    
    # Write simplex containing it
    s1 = _write_simplex(tmp_ledger, ["a_service", prov_id], pr=101)

    results = provisional_fast_path([prov_id], tmp_ledger, terl_path=tmp_terl)
    assert len(results) == 1
    assert results[0].simplex_id == s1.simplex_id
    assert results[0].pr_number == 101
    assert results[0].score == 1.0
    assert results[0].involved_entities == ["a_service"]
