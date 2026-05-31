import os
import sys
from pathlib import Path
import json

# Add src to python path so we can import modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from engine.engine import TessonEngine
from api.alert_schemas import AlertEvent

def main():
    data_dir = Path(__file__).parent / "data"
    ledger_path = data_dir / "proof_ledger.jsonl"
    terl_path = data_dir / "proof_terl.json"
    
    print(f"Loading data from:\nLedger: {ledger_path}\nTERL: {terl_path}")
    
    # Initialize engine
    engine = TessonEngine(
        ledger_path=ledger_path,
        terl_path=terl_path,
        recompile_interval_seconds=5.0
    )
    
    # Force initial compile
    print("Compiling TessonMatrix from data files...")
    engine.force_recompile()
    print(f"Matrix compiled successfully. Known entities: {engine._live_matrix.num_entities}")
    
    # We will simulate an alert on "frontend_service" and "cart_service"
    alert_payload = {
        "alert_id": "AL-12345",
        "alert_entities": ["FrontendService"],
        "severity": "high",
        "description": "Elevated 5xx error rates detected on frontend",
        "timestamp": 1776988650,
        "labels": {
            "environment": "production"
        }
    }
    
    print("\nSimulating Incoming Alert:")
    print(json.dumps(alert_payload, indent=2))
    
    alert = AlertEvent(**alert_payload)
    
    print("\nRunning Diffusion RCA...")
    # query_rca expects a list of entity string identifiers
    results = engine.query_rca(alert.alert_entities)
    
    print(f"\nRCA Engine found {len(results)} potential root causes.")
    
    # Print the top 5
    for idx, result in enumerate(results[:5]):
        print(f"\n[{idx+1}] Simplex ID: {result.simplex_id}")
        print(f"    Confidence Score: {result.score:.4f}")
        print(f"    Nodes Involved: {', '.join(result.involved_entities)}")
        print(f"    Commit: {result.commit_sha}")
        print(f"    PR: {result.pr_number}")

if __name__ == "__main__":
    main()
