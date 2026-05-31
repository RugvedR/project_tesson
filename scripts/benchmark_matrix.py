import time
from pathlib import Path
import sys
import numpy as np
from scipy.sparse import coo_matrix

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from engine.bridge import BridgeOutput
from engine.matrix import TessonMatrix
from engine.rca import RCAEngine

def run_benchmark():
    print("=== Tesson Math Engine Benchmark ===")
    print("Target: < 100ms for Compilation + Traversal on 10,000 entities")
    
    # Generate Synthetic Data
    V = 10000  # Number of entities
    S = 50000  # Number of simplices
    
    print(f"\nGenerating synthetic graph data (V={V}, S={S})...")
    # Each simplex connects 3 random entities
    np.random.seed(42)
    row_indices = np.random.randint(0, V, size=S * 3)
    col_indices = np.repeat(np.arange(S), 3)
    data = np.ones(S * 3, dtype=np.float32)
    
    # Fake mappings
    entity_to_index = {f"node_{i}": i for i in range(V)}
    index_to_entity = {i: f"node_{i}" for i in range(V)}
    simplex_to_index = {f"s_{i}": i for i in range(S)}
    index_to_simplex = {i: f"s_{i}" for i in range(S)}
    simplex_pr_map = {f"s_{i}": i for i in range(S)}
    
    bridge_output = BridgeOutput(
        row_indices=row_indices,
        col_indices=col_indices,
        data=data,
        entity_to_index=entity_to_index,
        index_to_entity=index_to_entity,
        simplex_to_index=simplex_to_index,
        index_to_simplex=index_to_simplex,
        num_entities=V,
        num_simplices=S,
        num_nonzero=len(data),
        skipped_simplices=0,
        projected_simplices=0,
        simplex_pr_map=simplex_pr_map,
        simplex_sha_map={}
    )
    
    # 1. Compilation
    print("\n1. Compiling CSR Matrices...")
    start_compile = time.perf_counter()
    matrix = TessonMatrix(bridge_output)
    end_compile = time.perf_counter()
    compile_time = (end_compile - start_compile) * 1000
    
    print(f"  Compile time: {compile_time:.2f}ms")
    print(f"  Boundary shape: {matrix.boundary.shape}")
    print(f"  Adjacency shape: {matrix.adjacency.shape} (nnz={matrix.adjacency.nnz})")
    
    # 2. RCA Traversal
    print("\n2. RCA Diffusion Traversal (3 hops)...")
    rca = RCAEngine(matrix)
    # Pick 2 random alerting nodes
    alert_nodes = [100, 5000]
    
    start_trace = time.perf_counter()
    results = rca.trace(alert_nodes, hops=3, top_k=5)
    end_trace = time.perf_counter()
    trace_time = (end_trace - start_trace) * 1000
    
    print(f"  Trace time: {trace_time:.2f}ms")
    print(f"  Top result score: {results[0].score:.2f}")
    
    total_time = compile_time + trace_time
    print(f"\n=== Benchmark Complete ===")
    print(f"Total Engine Latency: {total_time:.2f}ms")
    
    if total_time < 100:
        print("[SUCCESS]: Target of < 100ms achieved!")
    else:
        print("[FAILED]: Exceeded 100ms target.")

if __name__ == "__main__":
    run_benchmark()
