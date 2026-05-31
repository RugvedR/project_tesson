# Tesson Future Scope & Known Issues

This document tracks technical debt, edge cases, and future implementation requirements for the Tesson project.

## Future Architecture Plans

### 1. The End-State Architecture: Memory-Mapped Files (mmap)
**Description:** Currently, the Double-Buffering engine compiles the matrix and keeps it entirely in RAM. As the enterprise grows, storing a massive sparse matrix purely in RAM could become expensive, and recalculating it from scratch on boot takes time. To achieve persistent storage without the disk I/O penalty, Tesson should eventually transition to using **Memory-Mapped Files (mmap)**.
**Action:** 
- **Custom Binary Format:** Instead of storing the matrix as text or re-compiling it into RAM every time, compile the CSR matrix into a highly optimized, raw binary file (e.g., `tesson_matrix_state.tes`) and save it to the SSD.
- **The mmap Illusion:** When the Tesson server boots up, it uses the `mmap` system call. This tells the operating system kernel: "Pretend this file on the SSD is actually physical RAM."
- **The Result:** The matrix is persistent (safe from power outages). When an alert fires, the CPU runs the calculation directly against the virtual memory addresses. The OS kernel handles shuffling the bytes between the SSD and the RAM invisibly in the background. This gives the persistence of a hard drive with the calculation speed of RAM.

### 2. Entity Classification (Types)
**Description:** Currently, novel provisional entities are defaulted to `entity_type: "service"`.
**Action:** Implement a heuristic in the Math Engine (or a secondary LLM pipeline) to classify entities (e.g., distinguishing between a `service`, `database`, or `queue`) based on their edge directionality and connectivity in the matrix.

### 3. Bounded Memory Optimization Validation
**Description:** We implemented an optimization where the TERL clears the `seen_in_prs` array the moment a node hits `canonical` status.
**Action:** Monitor production memory usage of `terl_ledger.json` to ensure the file remains lightweight under heavy PR load.

---

## Resolved Debt (Historical)

- **[RESOLVED] Webhook Concurrency Race Condition:** Solved in Phase 2 Step 1 by wrapping the immutable `tesson_ledger.jsonl` and `terl_ledger.json` in cross-platform `filelock` objects.
- **[RESOLVED] Retroactive Graph Healing in the Math Engine:** Solved in Phase 2 Step 2 via the `CoordinateBridge`. The bridge dynamically iterates the entire ledger on every recompile. A node that was provisional last month (and skipped) but became canonical this month will suddenly validate all historical edges connected to it, healing the graph retroactively.
- **[RESOLVED] Matrix Dimensionality Explosion:** Solved in Phase 2 Step 2 via Subspace Projection. Provisional nodes are sliced out of the sparse matrix to guarantee perfect Math Engine purity and bound the dimensions of the vector space.
