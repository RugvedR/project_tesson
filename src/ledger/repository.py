"""
Tesson Storage Repository Pattern — Phase 2 Abstraction Layer

Defines abstract contracts for all persistence backends. The engine and API
code depend ONLY on these interfaces, never on concrete file paths or IO
operations. This enables a seamless Postgres migration in Phase 3 without
modifying a single line in the math engine.

Current implementation: File-based (JSONL ledger + JSON TERL)
Future implementation: PostgreSQL (asyncpg + connection pooling)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import Any

from ingestion.schemas import TessonSimplex


# ─── Ledger Repository ───────────────────────────────────────────────────────

class LedgerRepository(ABC):
    """Abstract contract for the immutable simplex ledger.

    The ledger is append-only. Existing records are never modified or deleted.
    Implementations must guarantee thread-safety on writes.
    """

    @abstractmethod
    def append_simplex(self, simplex: TessonSimplex) -> None:
        """Append a validated TessonSimplex to the ledger (thread-safe).

        Args:
            simplex: A fully validated TessonSimplex instance.

        Raises:
            IOError: If the write operation fails.
            filelock.Timeout: If the write lock cannot be acquired.
        """

    @abstractmethod
    def stream_simplices(self) -> Generator[TessonSimplex, None, None]:
        """Stream records from the ledger one at a time (memory-efficient).

        Yields each record as a validated TessonSimplex. Skips and logs any
        record that fails validation — the ledger is immutable so we can't
        fix them, but we also can't let one bad record crash the engine.

        Yields:
            Validated TessonSimplex objects.
        """

    @abstractmethod
    def read_all_simplices(self) -> list[TessonSimplex]:
        """Read and validate all records from the ledger.

        Returns:
            List of validated TessonSimplex objects. Corrupted records are
            skipped with a logged warning.
        """

    @abstractmethod
    def count_simplices(self) -> int:
        """Return the total number of valid records in the ledger."""


# ─── TERL Repository ─────────────────────────────────────────────────────────

class TERLRepository(ABC):
    """Abstract contract for the TERL persistence backend.

    The TERL state is a dictionary mapping canonical_id → entity metadata.
    Implementations must guarantee atomic writes (no partial state on disk).
    """

    @abstractmethod
    def load(self) -> dict[str, dict[str, Any]]:
        """Load the full TERL state from the persistence backend.

        Returns:
            Dictionary mapping canonical_id → entity metadata dict.
            Each entry has keys: entity_type, aliases, status, seen_in_prs.

        Raises:
            IOError: If the read operation fails.
            filelock.Timeout: If the read lock cannot be acquired.
        """

    @abstractmethod
    def save(self, store: dict[str, dict[str, Any]]) -> None:
        """Persist the full TERL state atomically.

        Args:
            store: The complete TERL state dictionary to write.

        Raises:
            IOError: If the write operation fails.
            filelock.Timeout: If the write lock cannot be acquired.
        """
