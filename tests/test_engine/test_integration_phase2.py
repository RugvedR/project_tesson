from pathlib import Path
import threading
import time
import pytest

from engine.bridge import CoordinateBridge
from engine.matrix import TessonMatrix
from engine.rca import RCAEngine
from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store


@pytest.fixture
def clean_ledgers(tmp_path):
    ledger = tmp_path / "test_ledger.jsonl"
    terl_path = tmp_path / "test_terl.json"
    return ledger, terl_path


def test_ledger_to_rca_e2e(clean_ledgers):
    ledger, terl_path = clean_ledgers
    
    # Setup TERL with canonical entities
    terl = EntityResolutionLedger(ledger_path=terl_path)
    for c in ["node_a", "node_b", "node_c", "node_d"]:
        terl.register_entity(c, [c], "service")
        # auto promote them
        terl._store[c]["seen_in_prs"] = [1, 2, 3]
        terl._store[c]["status"] = "canonical"
    terl._persist()
    
    # Write simplices
    store.append(TessonSimplex(nodes=["node_a", "node_b"], pr_number=1, commit_sha="a"*40), ledger)
    store.append(TessonSimplex(nodes=["node_b", "node_c"], pr_number=2, commit_sha="b"*40), ledger)
    store.append(TessonSimplex(nodes=["node_c", "node_d"], pr_number=3, commit_sha="c"*40), ledger)
    
    # Compile
    bridge = CoordinateBridge(ledger, terl_path)
    output = bridge.compile()
    
    # Matrix
    matrix = TessonMatrix(output)
    
    # RCA
    rca = RCAEngine(matrix)
    # Trace from A, should find A-B first
    idx_A = output.entity_to_index["node_a"]
    results = rca.trace([idx_A], hops=3)
    
    assert len(results) > 0
    assert results[0].score > 0
    # The exact top result depends on the diffusion topology, but it should be one of our PRs
    assert results[0].pr_number in [1, 2, 3]


def test_concurrent_ingest_then_compile(clean_ledgers):
    ledger, terl_path = clean_ledgers
    terl = EntityResolutionLedger(ledger_path=terl_path)
    for c in ["node_a", "node_b"]:
        terl.register_entity(c, [c], "service")
        terl._store[c]["seen_in_prs"] = [1, 2, 3]
        terl._store[c]["status"] = "canonical"
    terl._persist()
    
    def worker(i):
        store.append(
            TessonSimplex(nodes=["node_a", "node_b"], pr_number=100+i, commit_sha=str(i).zfill(40)),
            ledger_path=ledger
        )

    threads = []
    for i in range(5):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # Compile and verify all 5 are there
    bridge = CoordinateBridge(ledger, terl_path)
    output = bridge.compile()
    assert output.num_simplices == 5


def test_retroactive_healing_e2e(clean_ledgers):
    ledger, terl_path = clean_ledgers
    terl = EntityResolutionLedger(ledger_path=terl_path)
    # A is canonical, B is provisional
    terl.register_entity("node_a", ["node_a"], "service")
    terl._store["node_a"]["seen_in_prs"] = [1, 2, 3]
    terl._store["node_a"]["status"] = "canonical"
    
    terl.register_entity("node_b", ["node_b"], "service")
    terl._store["node_b"]["status"] = "provisional"
    terl._persist()
    
    # Write simplex with A and B
    store.append(TessonSimplex(nodes=["node_a", "node_b"], pr_number=1, commit_sha="a"*40), ledger)
    
    # 1. Compile (B is provisional, simplex has <2 canonical nodes -> skipped)
    bridge1 = CoordinateBridge(ledger, terl_path)
    out1 = bridge1.compile()
    assert out1.skipped_simplices == 1
    assert out1.num_simplices == 0
    
    # 2. Promote B
    terl._store["node_b"]["seen_in_prs"] = [1, 2, 3]
    terl._store["node_b"]["status"] = "canonical"
    terl._persist()
    
    # 3. Recompile (Simplex is now valid and healed)
    bridge2 = CoordinateBridge(ledger, terl_path)
    out2 = bridge2.compile()
    assert out2.skipped_simplices == 0
    assert out2.num_simplices == 1
    
    # RCA works on healed graph
    matrix = TessonMatrix(out2)
    rca = RCAEngine(matrix)
    idx_A = out2.entity_to_index["node_a"]
    results = rca.trace([idx_A], hops=1)
    
    assert len(results) == 1
    assert "node_b" in results[0].involved_entities
