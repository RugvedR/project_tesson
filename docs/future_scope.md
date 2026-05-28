# Tesson Future Scope & Known Issues

This document tracks technical debt, edge cases, and future implementation requirements for the Tesson project.

## Critical Priorities

### 1. Webhook Concurrency Race Condition
**Description:** The current pipeline reads and writes to `terl_ledger.json` and `tesson_ledger.jsonl` synchronously. When deployed as a live FastAPI webhook server, multiple PRs merging simultaneously will trigger concurrent pipeline runs. This will cause file overwrites and massive data corruption (lost provisional sightings, deleted aliases).
**Solution:** Implement rigorous File Locking (e.g., using Python's `filelock` library) around all ledger IO operations before deploying the API to production.

## Future Architecture Plans

### 2. Retroactive Graph Healing in the Math Engine
**Description:** Because the immutable ledger stores historical simplices, and the TERL tracks `provisional` -> `canonical` promotions, the Phase 2 Math Engine will inherently support retroactive graph healing.
**Action:** When building the Sparse Matrix Engine, ensure it queries the *current* TERL state for historical simplices. A node that was provisional last month (and skipped) but became canonical this month will suddenly validate all historical edges connected to it.

### 3. Entity Classification (Types)
**Description:** Currently, novel provisional entities are defaulted to `entity_type: "service"`.
**Action:** In Phase 2, implement a heuristic in the Math Engine (or a secondary LLM pipeline) to classify entities (e.g., distinguishing between a `service`, `database`, or `queue`) based on their edge directionality and connectivity in the matrix.

### 4. Bounded Memory Optimization Validation
**Description:** We implemented an optimization where the TERL clears the `seen_in_prs` array the moment a node hits `canonical` status.
**Action:** Monitor production memory usage of `terl_ledger.json` to ensure the file remains lightweight under heavy PR load.
