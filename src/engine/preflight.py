from __future__ import annotations

import logging
from dataclasses import dataclass

from scipy.sparse.csgraph import connected_components

from engine.matrix import TessonMatrix

logger = logging.getLogger(__name__)


@dataclass
class PreflightResult:
    has_cycle: bool
    betti_1: int
    cycle_entities: list[str]
    message: str


class PreflightChecker:
    """Detects topological cycles when new simplices are added."""

    def __init__(self, matrix: TessonMatrix) -> None:
        self.matrix = matrix

    def check(self, new_simplex_nodes: list[str]) -> PreflightResult:
        """
        Check if adding new_simplex_nodes to the matrix creates a topological cycle.
        
        1. Temporarily augment the boundary matrix with the new simplex
        2. Compute adjacency of augmented graph
        3. Calculate β₁ = E - V + C (for simple graphs)
        Does NOT mutate the live matrix.
        """
        if not new_simplex_nodes:
            return PreflightResult(False, 0, [], "Empty simplex provided.")

        # Resolve string nodes to matrix indices
        # We only consider canonical nodes that already exist in the matrix
        node_indices = [
            self.matrix._entity_to_index[node]
            for node in new_simplex_nodes
            if node in self.matrix._entity_to_index
        ]
        
        if len(node_indices) < 2:
            return PreflightResult(False, 0, [], "Less than 2 canonical nodes; no cycles possible.")

        # Number of vertices V is the number of entities in the current matrix
        V = self.matrix.num_entities
        
        # Build augmented adjacency matrix
        # Start with a copy of the existing adjacency matrix
        A_augmented = self.matrix.adjacency.copy()
        
        # A new simplex with N nodes adds an edge between every pair of those N nodes
        for i in range(len(node_indices)):
            for j in range(i + 1, len(node_indices)):
                u, v = node_indices[i], node_indices[j]
                A_augmented[u, v] = 1.0
                A_augmented[v, u] = 1.0
                
        # Calculate Betti-1 (number of 1-dimensional holes / cycles)
        # For a simple graph: Betti_1 = E - V + C
        # E = number of edges, V = number of vertices, C = connected components
        
        # Calculate E
        # A_augmented has non-zero entries for every edge.
        # Since A is symmetric, number of unique edges = (nnz - diagonal_nnz) / 2
        # We set diagonal to 0 and convert weights to 1 to just count topological edges
        A_augmented.setdiag(0)
        A_augmented.eliminate_zeros()
        A_augmented.data = A_augmented.data * 0 + 1.0
        
        E = A_augmented.nnz // 2
        
        # Calculate connected components (C)
        C, _ = connected_components(A_augmented, directed=False)
        
        betti_1 = E - V + C
        
        if betti_1 > 0:
            # Reconstruct the cycle entities
            # For simplicity in pre-flight, we return the nodes of the new simplex that triggered the cycle
            cycle_entities = [self.matrix._index_to_entity[idx] for idx in node_indices]
            msg = f"Cycle detected! Betti-1 increased to {betti_1}."
            return PreflightResult(True, betti_1, cycle_entities, msg)
        
        return PreflightResult(False, 0, [], "No topological cycles detected.")
