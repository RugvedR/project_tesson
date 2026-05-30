"""
Unit tests for the Tesson Coordinate Bridge.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from engine.bridge import CoordinateBridge, BridgeOutput
from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store


@pytest.fixture
def tmp_ledger(tmp_path):
    """Return a path to a temporary ledger file."""
    return tmp_path / "test_ledger.jsonl"


@pytest.fixture
def tmp_terl(tmp_path):
    """Return a path to a temporary TERL file."""
    return tmp_path / "test_terl.json"


def _write_simplex(ledger_path: Path, nodes: list[str], pr_number: int | None = None) -> TessonSimplex:
    """Create and append a simplex to the temporary ledger."""
    simplex = TessonSimplex(
        nodes=nodes,
        pr_number=pr_number,
        metadata={"test": "true"},
    )
    store.append(simplex, ledger_path=ledger_path)
    return simplex


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_simple_two_simplex_bridge(tmp_ledger, tmp_terl):
    """2 simplices, 3 canonical entities -> correct COO shapes and content."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")
    terl.register_entity("c_service", ["c_service"], "service")

    s1 = _write_simplex(tmp_ledger, ["a_service", "b_service"], pr_number=1)
    s2 = _write_simplex(tmp_ledger, ["b_service", "c_service"], pr_number=2)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()

    # Assertions on metrics
    assert out.num_entities == 3
    assert out.num_simplices == 2
    assert out.num_nonzero == 4
    assert out.skipped_simplices == 0
    assert out.projected_simplices == 0

    # Bidirectional maps
    assert set(out.entity_to_index.keys()) == {"a_service", "b_service", "c_service"}
    assert set(out.simplex_to_index.keys()) == {s1.simplex_id, s2.simplex_id}

    # Bidirectional maps inverse relation
    for entity, idx in out.entity_to_index.items():
        assert out.index_to_entity[idx] == entity
    for idx, entity in out.index_to_entity.items():
        assert out.entity_to_index[entity] == idx

    # COO matrices validation
    assert out.row_indices.shape == (4,)
    assert out.col_indices.shape == (4,)
    assert out.data.shape == (4,)
    assert np.all(out.data == 1)

    # Check mapping consistency
    # First simplex (col index 0) must contain a_service and b_service
    # Second simplex (col index 1) must contain b_service and c_service
    for r, c in zip(out.row_indices, out.col_indices):
        entity = out.index_to_entity[r]
        simplex_id = out.index_to_simplex[c]
        if simplex_id == s1.simplex_id:
            assert entity in ["a_service", "b_service"]
        else:
            assert entity in ["b_service", "c_service"]


def test_subspace_projection(tmp_ledger, tmp_terl):
    """Simplex [A, B, provisional_X] -> projected to [A, B] (kept, 2 nodes)."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")

    # This creates a new provisional entity
    prov_id = terl.resolve_entity("provisional_x", pr_number=101)
    assert terl.get_status(prov_id) == "provisional"

    s1 = _write_simplex(tmp_ledger, ["a_service", "b_service", prov_id], pr_number=1)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()

    assert out.num_entities == 2
    assert out.num_simplices == 1
    assert out.projected_simplices == 1
    assert out.skipped_simplices == 0
    assert prov_id not in out.entity_to_index


def test_dimensionality_gate_drops_singleton(tmp_ledger, tmp_terl):
    """Simplex [canonical_A, provisional_B] -> after removing provisional_B, only 1 node -> discarded."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    prov_id = terl.resolve_entity("provisional_x", pr_number=101)

    s1 = _write_simplex(tmp_ledger, ["a_service", prov_id], pr_number=1)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()

    assert out.num_entities == 0
    assert out.num_simplices == 0
    assert out.projected_simplices == 0
    assert out.skipped_simplices == 1
    assert out.row_indices.shape == (0,)


def test_retroactive_healing(tmp_ledger, tmp_terl):
    """Write simplex with provisional node -> compile (skipped) -> promote node -> recompile -> node now included."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    
    # Seen in PR 101: registered as provisional
    prov_id = terl.resolve_entity("provisional_x", pr_number=101)
    assert terl.get_status(prov_id) == "provisional"

    s1 = _write_simplex(tmp_ledger, ["a_service", prov_id], pr_number=101)

    # First compile: discarded because only 1 canonical node (a_service)
    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out1 = bridge.compile()
    assert out1.num_simplices == 0
    assert out1.skipped_simplices == 1

    # Promote the provisional entity to canonical by resolving in 2 more distinct PRs (total 3 PRs)
    terl.resolve_entity("provisional_x", pr_number=102)
    terl.resolve_entity("provisional_x", pr_number=103)
    assert terl.get_status(prov_id) == "canonical"

    # Second compile: now the simplex has 2 canonical nodes and is included!
    out2 = bridge.compile()
    assert out2.num_simplices == 1
    assert out2.skipped_simplices == 0
    assert out2.num_entities == 2
    assert prov_id in out2.entity_to_index


def test_empty_ledger(tmp_ledger, tmp_terl):
    """Empty file -> empty arrays, no crash."""
    # Ensure files exist but are empty or just have seed TERL
    terl = EntityResolutionLedger(ledger_path=tmp_terl)

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()

    assert out.num_entities == 0
    assert out.num_simplices == 0
    assert out.row_indices.shape == (0,)


def test_index_consistency(tmp_ledger, tmp_terl):
    """Verify index mapping consistency."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")

    s1 = _write_simplex(tmp_ledger, ["a_service", "b_service"])

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()

    for entity, idx in out.entity_to_index.items():
        assert out.index_to_entity[idx] == entity
    for simplex_id, idx in out.simplex_to_index.items():
        assert out.index_to_simplex[idx] == simplex_id
