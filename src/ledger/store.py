"""
Tesson Immutable Ledger Store — Layer 1 (Task 1.2 supporting component)

The ledger is the permanent, append-only record of every geometric interaction
ingested by the pipeline. It is the ground truth that the math engine reads.

Design constraints:
  - APPEND ONLY — existing records are never modified or deleted.
  - THREAD SAFE — cross-platform file locking via `filelock` guarantees
    that concurrent pipeline runs never interleave writes.
  - VALIDATION ON READ — every record read from disk is re-validated as a
    TessonSimplex (catches any disk corruption or schema drift).
  - JSONL FORMAT — one JSON object per line (streamable, no full-parse needed).

Storage Abstraction:
  This module implements the LedgerRepository interface defined in
  ledger.repository. The engine and API layers depend only on the abstract
  interface, enabling a seamless Postgres migration in Phase 3.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from pathlib import Path

from filelock import FileLock

from ingestion.schemas import TessonSimplex
from ledger.repository import LedgerRepository

logger = logging.getLogger(__name__)

# Default ledger path (overridden by LEDGER_PATH env var)
DEFAULT_LEDGER_PATH = Path(os.getenv("LEDGER_PATH", "./data/tesson_ledger.jsonl"))

# Lock timeout in seconds. If the lock cannot be acquired within this window,
# a filelock.Timeout is raised — the webhook layer catches this and lets
# GitHub retry the delivery automatically.
LOCK_TIMEOUT_SECONDS = 30


# ─── File-Based Ledger Repository ─────────────────────────────────────────────

class FileLedgerRepository(LedgerRepository):
    """File-based implementation of the LedgerRepository interface.

    Uses JSONL format with cross-platform file locking via `filelock`.
    """

    def __init__(self, ledger_path: Path | None = None) -> None:
        self._path = Path(ledger_path or DEFAULT_LEDGER_PATH)
        self._lock = FileLock(str(self._path) + ".lock", timeout=LOCK_TIMEOUT_SECONDS)

    def append_simplex(self, simplex: TessonSimplex) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = simplex.model_dump_json() + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        logger.debug("Ledger: appended simplex %s to %s", simplex.simplex_id, self._path)

    def stream_simplices(self) -> Generator[TessonSimplex, None, None]:
        if not self._path.exists():
            logger.info("Ledger at %s does not exist yet (no records).", self._path)
            return
        with self._path.open("r", encoding="utf-8") as f:
            for line_num, raw_line in enumerate(f, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    simplex = TessonSimplex.model_validate_json(raw_line)
                    yield simplex
                except Exception as e:
                    logger.warning(
                        "Ledger: skipping corrupted record at line %d: %s",
                        line_num, e,
                    )

    def read_all_simplices(self) -> list[TessonSimplex]:
        return list(self.stream_simplices())

    def count_simplices(self) -> int:
        return sum(1 for _ in self.stream_simplices())


# ─── Module-Level Convenience Functions ───────────────────────────────────────
# These preserve backward compatibility with the Phase 1 codebase.
# All callsites (graph.py, proof script, tests) use these functions.
# They delegate to a FileLedgerRepository instance internally.


def append(simplex: TessonSimplex, ledger_path: Path | None = None) -> None:
    """Append a validated TessonSimplex to the JSONL ledger (thread-safe).

    Args:
        simplex: A fully validated TessonSimplex instance.
        ledger_path: Override the default ledger path (useful for tests).

    Raises:
        IOError: If the file cannot be opened for writing.
        filelock.Timeout: If the write lock cannot be acquired within
            LOCK_TIMEOUT_SECONDS (default 30s).
    """
    repo = FileLedgerRepository(ledger_path)
    repo.append_simplex(simplex)


def read_all(ledger_path: Path | None = None) -> list[TessonSimplex]:
    """Read and validate all records from the ledger.

    Args:
        ledger_path: Override the default ledger path.

    Returns:
        List of validated TessonSimplex objects. Corrupted lines are skipped
        with a logged warning (never crash on read).
    """
    repo = FileLedgerRepository(ledger_path)
    return repo.read_all_simplices()


def stream(ledger_path: Path | None = None) -> Generator[TessonSimplex, None, None]:
    """Stream records from the ledger one at a time (memory-efficient).

    Yields each line as a validated TessonSimplex. Skips and logs any line
    that fails validation — the ledger is immutable so we can't fix them,
    but we also can't let one bad record crash the engine.

    Args:
        ledger_path: Override the default ledger path.

    Yields:
        Validated TessonSimplex objects.
    """
    repo = FileLedgerRepository(ledger_path)
    yield from repo.stream_simplices()


def count(ledger_path: Path | None = None) -> int:
    """Return the total number of valid records in the ledger."""
    repo = FileLedgerRepository(ledger_path)
    return repo.count_simplices()
