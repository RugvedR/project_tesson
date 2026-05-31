import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from engine.bridge import CoordinateBridge
from engine.engine import TessonEngine
from engine.matrix import TessonMatrix
from engine.preflight import PreflightChecker
from engine.rca import RCAEngine

def run_proof():
    print("=== Tesson Phase 2 Integration Proof ===")
    
    ledger_path = Path("data/proof_ledger.jsonl")
    terl_path = Path("data/proof_terl.json")
    
    if not ledger_path.exists() or not terl_path.exists():
        print(f"Error: Could not find ledger files in {ledger_path.parent}.")
        print("Please run scripts/run_phase1_proof.py first to generate the proof data.")
        return

    # 1. Coordinate Bridge
    print("\n1. Compiling Coordinate Bridge...")
    start = time.perf_counter()
    bridge = CoordinateBridge(ledger_path, terl_path)
    bridge_output = bridge.compile()
    print(f"  Compile time: {(time.perf_counter() - start)*1000:.2f}ms")
    print(f"  Entities (V): {bridge_output.num_entities}")
    print(f"  Simplices (S): {bridge_output.num_simplices}")
    print(f"  Projected Simplices: {bridge_output.projected_simplices}")
    print(f"  Skipped Simplices: {bridge_output.skipped_simplices}")
    
    # 2. CSR Matrices
    print("\n2. Building CSR Matrices...")
    start = time.perf_counter()
    matrix = TessonMatrix(bridge_output)
    print(f"  Build time: {(time.perf_counter() - start)*1000:.2f}ms")
    print(f"  Boundary Matrix B shape: {matrix.boundary.shape}")
    print(f"  Adjacency Matrix A shape: {matrix.adjacency.shape}")
    print(f"  Adjacency Non-zeros: {matrix.adjacency.nnz}")
    
    # 3. RCA Query (Canonical)
    print("\n3. Testing Matrix RCA (Canonical Entities)...")
    rca = RCAEngine(matrix)
    
    # Let's pick a known canonical entity from the microservices demo
    target_entity = "cart_service"
    
    # We need to map the string to the integer index
    if target_entity in bridge_output.entity_to_index:
        idx = bridge_output.entity_to_index[target_entity]
        start = time.perf_counter()
        results = rca.trace([idx])
        print(f"  Trace time: {(time.perf_counter() - start)*1000:.2f}ms")
        print(f"  Top RCA Result for '{target_entity}':")
        if results:
            print(f"    PR: {results[0].pr_number} | Score: {results[0].score:.4f} | Entities: {results[0].involved_entities}")
        else:
            print("    No results found.")
    else:
        print(f"  Skipped: '{target_entity}' not found in proof ledger.")

    # 4. Provisional Fast-Path (Engine Trapdoor)
    print("\n4. Testing Provisional Fast-Path via TessonEngine...")
    engine = TessonEngine(ledger_path, terl_path)
    engine.force_recompile()
    
    start = time.perf_counter()
    results = engine.query_rca(["commit"])  # 'commit' is the provisional entity from Phase 1
    print(f"  Trapdoor Query time: {(time.perf_counter() - start)*1000:.2f}ms")
    print(f"  Trapdoor Results for provisional entity 'commit':")
    for r in results:
         print(f"    PR: {r.pr_number} | Entities: {r.involved_entities}")

    # 5. Topological Preflight
    print("\n5. Testing Topological Preflight...")
    checker = PreflightChecker(matrix)
    # Test a synthetic cycle
    # We'll just grab the first 3 canonical entities and pretend they form a new PR
    if bridge_output.num_entities >= 3:
        e1 = bridge_output.index_to_entity[0]
        e2 = bridge_output.index_to_entity[1]
        e3 = bridge_output.index_to_entity[2]
        
        start = time.perf_counter()
        result = checker.check([e1, e2, e3])
        print(f"  Preflight check time: {(time.perf_counter() - start)*1000:.2f}ms")
        print(f"  Synthetic PR [{e1}, {e2}, {e3}] -> Cycle detected: {result.has_cycle}")
        if result.has_cycle:
             print(f"  {result.message}")
    
    print("\n=== Phase 2 Integration Proof Complete ===")

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    run_proof()
