"""
Tesson Sparse Matrix Compiler — Phase 2 Component

Compiles B (boundary incidence matrix) and A (adjacency matrix) from the
Coordinate Bridge's numerical COO arrays. Uses SciPy's Compressed Sparse Row (CSR)
format for memory efficiency and high-speed calculation.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix

from engine.bridge import BridgeOutput


class TessonMatrix:
    """Compiles B (boundary) and A (adjacency) from COO data."""

    def __init__(self, bridge_output: BridgeOutput) -> None:
        self.num_entities = bridge_output.num_entities
        self.num_simplices = bridge_output.num_simplices

        self._entity_to_index = bridge_output.entity_to_index
        self._index_to_entity = bridge_output.index_to_entity
        self._simplex_to_index = bridge_output.simplex_to_index
        self._index_to_simplex = bridge_output.index_to_simplex
        self._simplex_pr_map = bridge_output.simplex_pr_map
        self._simplex_sha_map = bridge_output.simplex_sha_map

        # Build B as a SciPy CSR matrix
        # Shape: (V, S) where V = num_entities, S = num_simplices
        shape = (self.num_entities, self.num_simplices)
        
        # If there are no entities or simplices, build an empty CSR matrix
        if self.num_entities == 0 or self.num_simplices == 0:
            self._B = csr_matrix(shape, dtype=np.float64)
            self._A = csr_matrix((self.num_entities, self.num_entities), dtype=np.float64)
        else:
            coo_B = coo_matrix(
                (bridge_output.data, (bridge_output.row_indices, bridge_output.col_indices)),
                shape=shape,
                dtype=np.float64
            )
            self._B = coo_B.tocsr()
            
            # Adjacency A = B @ B.T
            # Shape: (V, V)
            self._A = self._B.dot(self._B.T).tocsr()

    @property
    def boundary(self) -> csr_matrix:
        """Return the boundary incidence matrix B of shape (V, S)."""
        return self._B

    @property
    def adjacency(self) -> csr_matrix:
        """Return the symmetric adjacency matrix A of shape (V, V)."""
        return self._A

    def entity_degree(self, entity_id: str) -> int:
        """Return the number of simplices that the entity participates in.

        Corresponds to the diagonal entry A[i, i].
        """
        idx = self._entity_to_index.get(entity_id)
        if idx is None or self.num_entities == 0:
            return 0
        # A is a CSR matrix; accessing self._A[idx, idx] is fast
        return int(self._A[idx, idx])

    def stats(self) -> dict:
        """Return stats about the compiled matrices."""
        nnz = self._B.nnz
        total_cells = self.num_entities * self.num_simplices
        density = float(nnz / total_cells) if total_cells > 0 else 0.0

        return {
            "num_entities": self.num_entities,
            "num_simplices": self.num_simplices,
            "num_nonzero": nnz,
            "density": density,
        }
