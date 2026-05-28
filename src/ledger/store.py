"""
Tesson Immutable Ledger Store — Layer 1 (Task 1.2 supporting component)

The ledger is the permanent, append-only record of every geometric interaction
ingested by the pipeline. It is the ground truth that the math engine reads.

Design constraints:
  - APPEND ONLY — existing records are never modified or deleted.
  - THREAD SAFE — multiple concurrent pipeline runs may write simultaneously.
  - VALIDATION ON READ — every record read from disk is re-validated as a
    TessonSimplex (catches any disk corruption or schema drift).
  - JSONL FORMAT — one JSON object per line (streamable, no full-parse needed).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Generator, Optional

from ingestion.schemas import TessonSimplex

logger = logging.getLogger(__name__)

# Default ledger path (overridden by LEDGER_PATH env var)
DEFAULT_LEDGER_PATH = Path(os.getenv("LEDGER_PATH", "./data/tesson_ledger.jsonl"))


# ─── Write ────────────────────────────────────────────────────────────────────

def append(simplex: TessonSimplex, ledger_path: Optional[Path] = None) -> None:
    """Append a validated TessonSimplex to the JSONL ledger (thread-safe).

    Args:
        simplex: A fully validated TessonSimplex instance.
        ledger_path: Override the default ledger path (useful for tests).

    Raises:
        IOError: If the file cannot be opened for writing.
    """
    path = Path(ledger_path or DEFAULT_LEDGER_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = simplex.model_dump_json() + "\n"

    if sys.platform == "win32":
        # Windows: use a simple write (no fcntl available)
        _append_windows(path, line)
    else:
        _append_posix(path, line)

    logger.debug("Ledger: appended simplex %s to %s", simplex.simplex_id, path)


def _append_windows(path: Path, line: str) -> None:
    """Windows-compatible file append (no advisory locking)."""
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _append_posix(path: Path, line: str) -> None:
    """POSIX append with advisory file locking (Linux/macOS)."""
    import fcntl
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ─── Read ─────────────────────────────────────────────────────────────────────

def read_all(ledger_path: Optional[Path] = None) -> list[TessonSimplex]:
    """Read and validate all records from the ledger.

    Args:
        ledger_path: Override the default ledger path.

    Returns:
        List of validated TessonSimplex objects. Corrupted lines are skipped
        with a logged warning (never crash on read).
    """
    return list(stream(ledger_path))


def stream(ledger_path: Optional[Path] = None) -> Generator[TessonSimplex, None, None]:
    """Stream records from the ledger one at a time (memory-efficient).

    Yields each line as a validated TessonSimplex. Skips and logs any line
    that fails validation — the ledger is immutable so we can't fix them,
    but we also can't let one bad record crash the engine.

    Args:
        ledger_path: Override the default ledger path.

    Yields:
        Validated TessonSimplex objects.
    """
    path = Path(ledger_path or DEFAULT_LEDGER_PATH)

    if not path.exists():
        logger.info("Ledger at %s does not exist yet (no records).", path)
        return

    with path.open("r", encoding="utf-8") as f:
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


def count(ledger_path: Optional[Path] = None) -> int:
    """Return the total number of valid records in the ledger."""
    return sum(1 for _ in stream(ledger_path))
