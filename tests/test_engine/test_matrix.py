"""
Unit tests for the TessonMatrix compiler.
"""

from __future__ import annotations

from pathlib import Path
import numpy as np
import pytest

from engine.bridge import CoordinateBridge
from engine.matrix import TessonMatrix
from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store


@pytest.fixture
def tmp_ledger(tmp_path):
    return tmp_path / "test_ledger.jsonl"


@pytest.fixture
def tmp_terl(tmp_path):
    return tmp_path / "test_terl.json"


def _write_simplex(ledger_path: Path, nodes: list[str]) -> TessonSimplex:
    simplex = TessonSimplex(nodes=nodes, pr_number=1, metadata={"t": "t"})
    store.append(simplex, ledger_path=ledger_path)
    return simplex


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_boundary_matrix_shape(tmp_ledger, tmp_terl):
    """3 entities, 2 simplices -> B.shape == (3, 2)."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")
    terl.register_entity("c_service", ["c_service"], "service")

    _write_simplex(tmp_ledger, ["a_service", "b_service"])
    _write_simplex(tmp_ledger, ["b_service", "c_service"])

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)

    assert matrix.boundary.shape == (3, 2)
    assert matrix.adjacency.shape == (3, 3)


def test_adjacency_symmetry(tmp_ledger, tmp_terl):
    """A == A.T (symmetry of the adjacency matrix)."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("a_service", ["a_service"], "service")
    terl.register_entity("b_service", ["b_service"], "service")
    terl.register_entity("c_service", ["c_service"], "service")

    _write_simplex(tmp_ledger, ["a_service", "b_service", "c_service"])

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)

    A = matrix.adjacency.toarray()
    assert np.allclose(A, A.T)


def test_adjacency_diagonal(tmp_ledger, tmp_terl):
    """Diagonal entries A[i, i] equal the participation counts of each entity."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("entity_a", ["entity_a"], "service")
    terl.register_entity("entity_b", ["entity_b"], "service")
    terl.register_entity("entity_c", ["entity_c"], "service")

    # entity_a appears in 3 simplices, entity_b in 2, entity_c in 1
    _write_simplex(tmp_ledger, ["entity_a", "entity_b"])
    _write_simplex(tmp_ledger, ["entity_a", "entity_c"])
    _write_simplex(tmp_ledger, ["entity_a", "entity_b"])

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)

    assert matrix.entity_degree("entity_a") == 3
    assert matrix.entity_degree("entity_b") == 2
    assert matrix.entity_degree("entity_c") == 1
    assert matrix.entity_degree("nonexistent") == 0


def test_disconnected_components(tmp_ledger, tmp_terl):
    """2 isolated pairs -> A contains 2 disconnected blocks (no path between them)."""
    terl = EntityResolutionLedger(ledger_path=tmp_terl)
    terl.register_entity("entity_a", ["entity_a"], "service")
    terl.register_entity("entity_b", ["entity_b"], "service")
    terl.register_entity("entity_c", ["entity_c"], "service")
    terl.register_entity("entity_d", ["entity_d"], "service")

    _write_simplex(tmp_ledger, ["entity_a", "entity_b"])
    _write_simplex(tmp_ledger, ["entity_c", "entity_d"])

    bridge = CoordinateBridge(ledger_path=tmp_ledger, terl_path=tmp_terl)
    out = bridge.compile()
    matrix = TessonMatrix(out)

    A = matrix.adjacency.toarray()
    
    # Check that there is no adjacency connection between (entity_a, entity_b) block and (entity_c, entity_d) block.
    # Sorted indices alphabetically: entity_a=0, entity_b=1, entity_c=2, entity_d=3.
    # Therefore, A[0, 2], A[0, 3], A[1, 2], A[1, 3] must all be 0.
    assert A[0, 2] == 0.0
    assert A[0, 3] == 0.0
    assert A[1, 2] == 0.0
    assert A[1, 3] == 0.0


def test_from_proof_data():
    """Real proof data shapes and density checks."""
    project_root = Path(__file__).parent.parent.parent
    ledger_path = project_root / "data" / "proof_ledger.jsonl"
    terl_path = project_root / "data" / "proof_terl.json"

    bridge = CoordinateBridge(ledger_path=ledger_path, terl_path=terl_path)
    out = bridge.compile()
    matrix = TessonMatrix(out)

    stats = matrix.stats()
    assert stats["num_entities"] == 15
    assert stats["num_simplices"] == 48
    assert stats["num_nonzero"] == 127
    assert stats["density"] > 0.0
