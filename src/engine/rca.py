"""
Tesson Root Cause Traversal Engine — Phase 2 Component

Implements two main traversal paths:
  1. Matrix Diffusion: A^k * y propagation over the symmetric adjacency matrix,
     mapping node scores back to simplices via B^T.
  2. Provisional Fast-Path (The Trapdoor): Direct O(n) scan of the immutable
     ledger for provisional entities, bypassing the matrix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engine.matrix import TessonMatrix
from ingestion.schemas import TessonSimplex
from ingestion.terl import EntityResolutionLedger
from ledger import store

logger = logging.getLogger(__name__)


@dataclass
class RCAResult:
    """A candidate root cause identified by the traversal engine."""
    commit_sha: str | None
    pr_number: int | None
    simplex_id: str
    score: float
    involved_entities: list[str]


class RCAEngine:
    """Root Cause Analysis engine operating on the compiled sparse matrices."""

    def __init__(self, matrix: TessonMatrix) -> None:
        self.matrix = matrix

    def trace(
        self,
        alert_entity_indices: list[int],
        hops: int = 3,
        top_k: int = 5,
    ) -> list[RCAResult]:
        """Runs the matrix diffusion algorithm.

        Algorithm:
          1. Build a binary one-hot vector y of size V.
          2. Compute v_diffuse = A^hops * y.
          3. Project node scores back to simplices: s_scores = B^T * v_diffuse.
          4. Rank simplices by score descending and return the top_k.
        """
        V = self.matrix.num_entities
        S = self.matrix.num_simplices

        if V == 0 or S == 0 or not alert_entity_indices:
            return []

        # 1. Build binary starting vector y
        y = np.zeros(V, dtype=np.float64)
        for idx in alert_entity_indices:
            if 0 <= idx < V:
                y[idx] = 1.0

        # 2. Diffusion: v_diffuse = A^hops * y
        v = y.copy()
        A = self.matrix.adjacency
        for _ in range(hops):
            v = A.dot(v)

        # 3. Map scores back to simplices: s_scores = B^T * v_diffuse
        # B is shape (V, S), so B.T is (S, V).
        # s_scores is shape (S,)
        B_T = self.matrix.boundary.T
        s_scores = B_T.dot(v)

        # 4. Rank simplices
        # Get indices of simplices sorted by score descending
        sorted_indices = np.argsort(s_scores)[::-1]

        # Convert boundary matrix to CSC for fast column/simplex lookup
        B_csc = self.matrix.boundary.tocsc()

        results = []
        for idx in sorted_indices:
            score = float(s_scores[idx])
            # Only return simplices that have some diffusion connection
            if score <= 0.0:
                continue

            simplex_id = self.matrix._index_to_simplex[idx]
            pr_number = self.matrix._simplex_pr_map.get(simplex_id)
            commit_sha = self.matrix._simplex_sha_map.get(simplex_id)

            # Reconstruct the involved canonical entities in this simplex
            node_indices = B_csc.indices[B_csc.indptr[idx]:B_csc.indptr[idx+1]]
            involved = [self.matrix._index_to_entity[n_idx] for n_idx in node_indices]

            results.append(
                RCAResult(
                    commit_sha=commit_sha,
                    pr_number=pr_number,
                    simplex_id=simplex_id,
                    score=score,
                    involved_entities=involved,
                )
            )

            if len(results) >= top_k:
                break

        return results


def provisional_fast_path(
    alert_entities: list[str],
    ledger_path: Path,
    terl_path: Path | None = None,
) -> list[RCAResult]:
    """Scans the ledger directly for any simplices containing provisional entities.

    Used as the Trapdoor routing fork because provisional entities have extremely
    low frequency (<= 2 sightings) and don't participate in the main matrix.
    """
    if not alert_entities:
        return []

    # Initialize TERL to extract statuses of involved entities
    terl = EntityResolutionLedger(ledger_path=terl_path)
    alert_set = set(alert_entities)
    results = []
    seen_simplex_ids = set()

    for simplex in store.stream(ledger_path):
        # If the simplex intersects with the alert entities, it is a candidate
        if alert_set.intersection(simplex.nodes):
            if simplex.simplex_id not in seen_simplex_ids:
                seen_simplex_ids.add(simplex.simplex_id)
                # Keep only canonical nodes for involved_entities
                involved = [
                    node for node in simplex.nodes
                    if terl.get_status(node) == "canonical"
                ]
                results.append(
                    RCAResult(
                        commit_sha=simplex.commit_sha,
                        pr_number=simplex.pr_number,
                        simplex_id=simplex.simplex_id,
                        # Default high confidence score of 1.0 for direct match
                        score=1.0,
                        involved_entities=involved,
                    )
                )

    return results
