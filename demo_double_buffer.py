import os
import sys
import time
import shutil
import tempfile
from pathlib import Path

# Add src to python path so we can import modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from engine.engine import TessonEngine
from api.alert_schemas import AlertEvent
from ingestion.schemas import TessonSimplex
from ledger import store

def main():
    print("=== Tesson Double-Buffering & Async Compilation Demo ===\n")
    
    # 1. Setup temporary workspace so we don't pollute the real data
    temp_dir = Path(tempfile.mkdtemp(prefix="tesson_demo_"))
    data_dir = Path(__file__).parent / "data"
    
    ledger_path = temp_dir / "ledger.jsonl"
    terl_path = temp_dir / "terl.json"
    
    shutil.copy(data_dir / "proof_ledger.jsonl", ledger_path)
    shutil.copy(data_dir / "proof_terl.json", terl_path)
    
    print(f"1. Cloned proof data to temporary directory: {temp_dir}")
    
    # 2. Initialize Engine with a 2-second background worker interval
    print("2. Initializing TessonEngine (recompile interval = 2 seconds)...")
    engine = TessonEngine(
        ledger_path=ledger_path,
        terl_path=terl_path,
        recompile_interval_seconds=2.0
    )
    
    # Ensure it's compiled once synchronously to start
    engine.force_recompile()
    initial_simplices = engine._live_matrix.num_simplices
    print(f"   Baseline Matrix Compiled: {initial_simplices} simplices (PRs) loaded.\n")
    
    # 3. Start the background worker
    print("3. Starting the Asynchronous Background Worker Thread...")
    engine.start_background_worker()
    
    # 4. Perform a baseline query
    alert = AlertEvent(
        alert_id="TEST-1",
        alert_entities=["FrontendService"],
        severity="high"
    )
    
    print("\n   [Live Queries vs Matrix State]")
    print(f"   Continuously querying RCA for {alert.alert_entities[0]} every 0.5s...")
    print("   Watch how queries NEVER block, and how the results instantly change when the pointer swaps!\n")
    
    # 5. Loop to show continuous querying
    for i in range(4):
        results = engine.query_rca(alert.alert_entities)
        top_pr = results[0].pr_number if results else "None"
        num_simplices = engine._live_matrix.num_simplices
        print(f"   [Query {i+1}] Matrix Simplices: {num_simplices} | Top Root Cause PR: {top_pr}")
        time.sleep(0.5)
        
    # 6. Inject a new massive PR into the ledger
    print("\n4. INJECTING MASSIVE NEW PULL REQUEST (PR #9999) INTO LEDGER...")
    print("   (This PR connects frontend_service to 5 other services simultaneously)")
    
    new_simplex = TessonSimplex(
        nodes=[
            "frontend_service", "cart_service", "redis_cache", "checkout_service", 
            "payment_service", "postgres_db", "ad_service", "currency_service",
            "email_service", "load_generator", "product_catalog_service",
            "recommendation_service", "shipping_service", "mongodb", "kafka"
        ],
        pr_number=9999,
        commit_sha="abcdef0123456789abcdef0123456789abcdef01"
    )
    # Note: store.append uses filelock to safely write
    store.append(new_simplex, ledger_path=ledger_path)
    
    # 7. Continue querying to watch the swap
    print("\n   Resuming queries (Worker thread is compiling in the background)...")
    for i in range(10):
        # We query the engine without ANY locks or blocking
        start_t = time.perf_counter()
        results = engine.query_rca(alert.alert_entities)
        latency = (time.perf_counter() - start_t) * 1000
        
        top_pr = results[0].pr_number if results else "None"
        num_simplices = engine._live_matrix.num_simplices
        
        # Highlight when the swap happens
        status = "<- POINTER SWAPPED!" if num_simplices > initial_simplices else ""
        
        print(f"   [Query {i+5}] Latency: {latency:.2f}ms | Matrix Simplices: {num_simplices} | Top Root Cause PR: {top_pr} {status}")
        time.sleep(0.5)
        
    print("\n5. Cleaning up and stopping worker...")
    engine.stop_background_worker()
    shutil.rmtree(temp_dir)
    print("=== Demo Complete ===")

if __name__ == "__main__":
    main()
