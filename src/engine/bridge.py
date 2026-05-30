"""
Tesson Coordinate Bridge — Phase 2 Component

Translates the string-based JSONL ledger into raw numeric COO arrays.
Implements Subspace Projection with Dimensionality Gating:
  1. Read each simplex from the ledger.
  2. For each node in the simplex, check its TERL status.
  3. Drop provisional nodes from the node array (in RAM only).
  4. If len(remaining_nodes) >= 2: keep the simplex (projected).
  5. If len(remaining_nodes) < 2: discard the simplex.
  6. Never modify the source JSONL file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import numpy as np

from ingestion.terl import EntityResolutionLedger
from ledger import store


@dataclass
class BridgeOutput:
    """The output of a CoordinateBridge compilation."""
    # COO arrays for the boundary matrix B
    row_indices: np.ndarray       # (nnz,) — entity integer indices
    col_indices: np.ndarray       # (nnz,) — simplex integer indices
    data: np.ndarray              # (nnz,) — all 1s (incidence)

    # Bidirectional mappings
    entity_to_index: dict[str, int]
    index_to_entity: dict[int, str]
    simplex_to_index: dict[str, int]
    index_to_simplex: dict[int, str]

    # Stats
    num_entities: int             # V (rows in B)
    num_simplices: int            # S (columns in B)
    num_nonzero: int
    skipped_simplices: int        # Dropped because < 2 canonical nodes after projection
    projected_simplices: int      # Kept but with provisional nodes removed

    # Traceability maps (for RCA output)
    simplex_pr_map: dict[str, int | None]
    simplex_sha_map: dict[str, str | None]


class CoordinateBridge:
    """Translates the JSONL string ledger → numeric COO arrays."""

    def __init__(
        self,
        ledger_path: Path | None = None,
        terl_path: Path | None = None,
    ) -> None:
        self.ledger_path = Path(ledger_path) if ledger_path else store.DEFAULT_LEDGER_PATH
        self.terl_path = Path(terl_path) if terl_path else Path(os.getenv("TERL_PATH", "./data/terl_ledger.json"))

    def compile(self) -> BridgeOutput:
        """Reads the ledger and TERL state, applies Subspace Projection, and returns numeric COO data."""
        # Load the current TERL state
        # EntityResolutionLedger constructor automatically loads from self.terl_path if it exists
        terl = EntityResolutionLedger(ledger_path=self.terl_path)

        skipped_simplices = 0
        projected_simplices = 0
        kept_simplices = []

        # Stream and filter simplices
        for simplex in store.stream(self.ledger_path):
            canonical_nodes = [
                node for node in simplex.nodes
                if terl.get_status(node) == "canonical"
            ]

            if len(canonical_nodes) >= 2:
                if len(canonical_nodes) < len(simplex.nodes):
                    projected_simplices += 1
                kept_simplices.append(
                    (simplex.simplex_id, canonical_nodes, simplex.pr_number, simplex.commit_sha)
                )
            else:
                skipped_simplices += 1

        # Build bidirectional mappings
        # To make indices highly deterministic, sort entities alphabetically
        unique_entities = sorted(list(set(
            node for _, nodes, _, _ in kept_simplices for node in nodes
        )))
        entity_to_index = {entity: idx for idx, entity in enumerate(unique_entities)}
        index_to_entity = {idx: entity for entity, idx in entity_to_index.items()}

        # Keep the original ledger order for simplices
        simplex_to_index = {
            s_id: idx for idx, (s_id, _, _, _) in enumerate(kept_simplices)
        }
        index_to_simplex = {idx: s_id for s_id, idx in simplex_to_index.items()}

        # Build COO arrays
        row_list = []
        col_list = []
        data_list = []

        for col_idx, (simplex_id, nodes, _, _) in enumerate(kept_simplices):
            for node in nodes:
                row_idx = entity_to_index[node]
                row_list.append(row_idx)
                col_list.append(col_idx)
                data_list.append(1)

        row_indices = np.array(row_list, dtype=np.int32)
        col_indices = np.array(col_list, dtype=np.int32)
        data = np.array(data_list, dtype=np.int32)

        simplex_pr_map = {s_id: pr for s_id, _, pr, _ in kept_simplices}
        simplex_sha_map = {s_id: sha for s_id, _, _, sha in kept_simplices}

        return BridgeOutput(
            row_indices=row_indices,
            col_indices=col_indices,
            data=data,
            entity_to_index=entity_to_index,
            index_to_entity=index_to_entity,
            simplex_to_index=simplex_to_index,
            index_to_simplex=index_to_simplex,
            num_entities=len(unique_entities),
            num_simplices=len(kept_simplices),
            num_nonzero=len(row_list),
            skipped_simplices=skipped_simplices,
            projected_simplices=projected_simplices,
            simplex_pr_map=simplex_pr_map,
            simplex_sha_map=simplex_sha_map,
        )
