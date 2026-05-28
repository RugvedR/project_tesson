# Tesson Project Design Document

## Overview
Tesson is an automated system designed to ingest pull requests and deterministically build a sparse matrix model of enterprise microservice architectures. By analyzing code changes using an LLM as a semantic parser, Tesson constructs an immutable mathematical ledger of system topologies.

## Core Architecture
The system follows a pipeline architecture orchestrated by LangGraph.

1. **Hydration (Deterministic Baseline):**
   Fetches the raw Git diff, merge timestamp, and canonical commit SHA from the GitHub API. This ensures deterministic, un-hallucinatable metadata.
2. **LLM Extraction (Semantic Parser):**
   The LLM parses the Git diff to identify touched microservices. It is strictly constrained by a list of known "canonical" entities to minimize drift.
3. **Tesson Entity Resolution Ledger (TERL):**
   The AI firewall. The TERL intercepts LLM outputs, collapsing naming chaos (e.g., `redis-cart` vs `redis_cache`) into canonical Matrix Row IDs.
4. **Ledger (Immutable Fact Table):**
   The final resolved `TessonSimplex` is appended to `tesson_ledger.jsonl`.

## The Hybrid TERL Approach
To balance 100% automated ingestion velocity with the mathematical purity required by the sparse matrix engine, Tesson employs a Hybrid TERL approach.

### The "Rule of 3" Auto-Promotion
- **Provisional Status:** When the LLM outputs a brand-new entity, it is quarantined in the TERL with a `provisional` status.
- **Sighting Tracking:** The TERL tracks which PRs touch this provisional entity.
- **Auto-Promotion:** If the exact provisional entity is seen in 3 distinct PRs, it is mathematically verified as real infrastructure and automatically promoted to `canonical` status.
- **Fuzzy Aggregation:** Rapidfuzz (>92% WRatio) ensures that slight LLM inconsistencies (e.g. `shopping_cart` vs `shoppingcart`) are grouped under the same provisional ID, allowing accurate Rule of 3 tracking.
- **LLM Prompting:** Provisional entities are hidden from the LLM prompt to prevent hallucination feedback loops.

## Implementation Phases & Milestones

### Phase 1: Ingestion & Bedrock (Completed)
- **Goal:** Safely parse PRs, extract nodes, and build an immutable ledger.
- **Milestone 1 Validation:** 100% schema integrity, 100% TERL collapse (no duplicate nodes for the same service), 100% deterministic metadata retention.
- **Audit Logging:** Every PR ingestion generates a JSON audit log detailing the LLM input, the LLM raw output, and the final resolved simplex for complete traceability.

### Phase 2: Sparse Matrix Math Engine (Future)
- **Goal:** Ingest the `tesson_ledger.jsonl` to build a mathematical representation of the architecture.
- **Rule:** The Math Engine will explicitly filter out any nodes where `status == "provisional"` in the TERL, ensuring absolute matrix purity.
