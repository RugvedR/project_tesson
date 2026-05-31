"""
Tesson Runtime Engine — Phase 2 Component

Orchestrates the active matrix representation in memory. Implements the
Asynchronous Double-Buffering pattern using a background worker thread.
RCA queries run concurrently against the live matrix and never block on re-compilation.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from engine.bridge import CoordinateBridge
from engine.matrix import TessonMatrix
from engine.rca import RCAEngine, RCAResult, provisional_fast_path
from ingestion.terl import EntityResolutionLedger
from ledger import store

logger = logging.getLogger(__name__)


class TessonEngine:
    """Asynchronous Double-Buffering runtime engine for Tesson sparse matrices."""

    def __init__(
        self,
        ledger_path: Path | None = None,
        terl_path: Path | None = None,
        recompile_interval_seconds: float = 60.0,
    ) -> None:
        self._ledger_path = Path(ledger_path) if ledger_path else store.DEFAULT_LEDGER_PATH
        self._terl_path = Path(terl_path) if terl_path else Path(os.getenv("TERL_PATH", "./data/terl_ledger.json"))
        self.recompile_interval_seconds = recompile_interval_seconds

        self._live_matrix: TessonMatrix | None = None
        self._live_rca: RCAEngine | None = None
        self._lock = threading.Lock()

        # Background thread state
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Initial compile to populate live matrices immediately on startup
        try:
            self._recompile()
        except Exception as e:
            logger.warning(
                "Initial matrix compilation failed (this is expected if ledger files are missing): %s",
                e
            )

    def start_background_worker(self) -> None:
        """Start the background thread that periodically recompiles the matrix."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                logger.warning("TessonEngine background worker is already running.")
                return

            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="TessonEngineWorker",
                daemon=True
            )
            self._worker_thread.start()
            logger.info("TessonEngine background worker started successfully.")

    def stop_background_worker(self) -> None:
        """Stop the background recompile thread and wait for it to exit."""
        thread_to_join = None
        with self._lock:
            if self._worker_thread is not None:
                self._stop_event.set()
                thread_to_join = self._worker_thread
                self._worker_thread = None

        if thread_to_join is not None:
            thread_to_join.join(timeout=5.0)
            logger.info("TessonEngine background worker stopped.")

    def force_recompile(self) -> None:
        """Manually trigger a synchronous matrix recompilation."""
        self._recompile()

    def _recompile(self) -> None:
        """Compiles a new staging matrix and atomically swaps it into live service."""
        bridge = CoordinateBridge(self._ledger_path, self._terl_path)
        output = bridge.compile()
        staging_matrix = TessonMatrix(output)
        staging_rca = RCAEngine(staging_matrix)

        # Pre-flight check: Detect cycles in new simplices
        if self._live_matrix is not None and self._live_matrix.num_simplices > 0:
            from engine.preflight import PreflightChecker
            from ingestion.schemas import TessonAnnotation
            
            checker = PreflightChecker(self._live_matrix)
            B_csc = staging_matrix.boundary.tocsc()
            
            for i in range(staging_matrix.num_simplices):
                simplex_id = staging_matrix._index_to_simplex[i]
                if simplex_id not in self._live_matrix._simplex_pr_map:
                    # Found a new simplex since the last compilation
                    node_indices = B_csc.getcol(i).indices
                    nodes = [staging_matrix._index_to_entity[idx] for idx in node_indices]
                    
                    result = checker.check(nodes)
                    if result.has_cycle:
                        logger.warning(
                            "Pre-flight Cycle Detected for simplex %s: %s Entities: %s",
                            simplex_id, result.message, result.cycle_entities
                        )
                        annotation = TessonAnnotation(
                            annotation_type="cycle_detected",
                            target_simplex_id=simplex_id,
                            details={"betti_1": str(result.betti_1), "entities": ",".join(result.cycle_entities)}
                        )
                        store.append_annotation(annotation, ledger_path=self._ledger_path)

        with self._lock:
            self._live_matrix = staging_matrix
            self._live_rca = staging_rca

        logger.debug(
            "Matrix recompiled: %d entities, %d simplices",
            staging_matrix.num_entities,
            staging_matrix.num_simplices
        )

    def _worker_loop(self) -> None:
        """Target loop for the background compilation thread."""
        while not self._stop_event.is_set():
            # Wait for the specified interval, waking up early if stop_background_worker is called
            stopped = self._stop_event.wait(self.recompile_interval_seconds)
            if stopped or self._stop_event.is_set():
                break

            try:
                self._recompile()
            except Exception as e:
                logger.exception("Error during background matrix compilation: %s", e)

    def query_rca(
        self,
        alert_entities: list[str],
        hops: int = 3,
        top_k: int = 5,
    ) -> list[RCAResult]:
        """Routes an alert query through matrix diffusion and/or the provisional fast-path.

        Args:
            alert_entities: List of entity IDs associated with the alert.
            hops: Diffusion traversal steps (default: 3).
            top_k: Max candidate causes to return (default: 5).

        Returns:
            Ranked list of RCAResult objects.
        """
        # Ensure we have a compiled live matrix
        if self._live_matrix is None:
            with self._lock:
                if self._live_matrix is None:
                    try:
                        self._recompile()
                    except Exception as e:
                        logger.error("Failed to compile live matrix on demand: %s", e)
                        return []

        # Access live reference atomically
        with self._lock:
            live_matrix = self._live_matrix
            live_rca = self._live_rca

        if live_matrix is None or live_rca is None:
            return []

        # Split alerts by TERL status
        terl = EntityResolutionLedger(ledger_path=self._terl_path)
        canonical_alerts = []
        provisional_alerts = []

        for entity in alert_entities:
            # Use read-only lookup that performs both exact and fuzzy matching
            # without mutating the TERL ledger for unknown entities.
            resolved = terl.lookup_entity(entity)
            if resolved is None:
                # If completely unknown, treat as a raw provisional entity
                resolved = entity
                
            if terl.get_status(resolved) == "canonical":
                canonical_alerts.append(resolved)
            else:
                provisional_alerts.append(resolved)

        matrix_results: list[RCAResult] = []
        provisional_results: list[RCAResult] = []

        # 1. Process Canonical Entities via Matrix Adjacency Diffusion
        if canonical_alerts:
            # Map canonical string IDs to matrix integer row indices
            entity_indices = [
                live_matrix._entity_to_index[entity]
                for entity in canonical_alerts
                if entity in live_matrix._entity_to_index
            ]
            if entity_indices:
                matrix_results = live_rca.trace(entity_indices, hops=hops, top_k=top_k)

        # 2. Process Provisional Entities via Direct Ledger Lookup
        if provisional_alerts:
            provisional_results = provisional_fast_path(
                provisional_alerts,
                self._ledger_path,
                self._terl_path
            )

        # 3. Merge Results
        # Remove duplicates by preserving the higher score for any overlapping simplex
        merged_map: dict[str, RCAResult] = {}

        for result in matrix_results + provisional_results:
            existing = merged_map.get(result.simplex_id)
            if existing is None or result.score > existing.score:
                merged_map[result.simplex_id] = result

        # Sort combined results by score descending
        sorted_results = sorted(merged_map.values(), key=lambda r: r.score, reverse=True)

        return sorted_results[:top_k]
