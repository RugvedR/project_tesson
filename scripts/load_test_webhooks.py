import concurrent.futures
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ingestion.schemas import TessonSimplex
from ledger import store
from ledger.store import FileLedgerRepository

def worker(worker_id: int, ledger_path: Path):
    """Simulate a webhook payload being appended."""
    # Generate a valid 40-char hex string
    hex_id = str(worker_id).zfill(40)
    simplex = TessonSimplex(
        commit_sha=hex_id,
        pr_number=9000 + worker_id,
        nodes=[f"service_{worker_id}", "common_db"]
    )
    # Using the standard append which utilizes filelock
    try:
        store.append(simplex, ledger_path=ledger_path)
        return True
    except Exception as e:
        print(f"Worker {worker_id} failed: {e}")
        return False

def run_load_test():
    print("=== Tesson Webhook Concurrency Load Test ===")
    
    test_ledger = Path("data/test_concurrent_ledger.jsonl")
    # Clean up any old file
    if test_ledger.exists():
        test_ledger.unlink()
        
    num_requests = 20
    print(f"Simulating {num_requests} simultaneous GitHub webhooks...")
    
    start_time = time.perf_counter()
    
    success_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [executor.submit(worker, i, test_ledger) for i in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                success_count += 1
                
    end_time = time.perf_counter()
    
    print(f"\nCompleted in {(end_time - start_time)*1000:.2f}ms")
    print(f"Successful appends: {success_count}/{num_requests}")
    
    # Verify the file contents
    repo = FileLedgerRepository(test_ledger)
    final_count = repo.count_simplices()
    print(f"Total simplices in ledger file: {final_count}")
    
    if final_count == num_requests:
        print("[SUCCESS]: No file corruption or lost entries. File locking works perfectly.")
    else:
        print(f"[FAILED]: Expected {num_requests} entries but found {final_count}.")

if __name__ == "__main__":
    run_load_test()
