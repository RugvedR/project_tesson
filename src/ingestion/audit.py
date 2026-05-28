"""
Tesson Pipeline Audit Logging System — Traceability

Provides full transparency into the LLM's inputs and outputs.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ingestion.schemas import TessonSimplex

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, logs_dir: Path | str = "./data/audit_logs"):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def write_log(
        self,
        repo: str,
        pr_number: int,
        diff_text: str | None,
        commit_sha: str | None,
        merged_at_timestamp: int | None,
        llm_raw_output: dict | None,
        resolved_simplex: TessonSimplex | None,
        pipeline_error: str | None,
    ) -> Path:
        """Write a full audit trace of a PR ingestion to disk."""
        log_file = self.logs_dir / f"pr_{pr_number}_audit.json"
        
        audit_data: dict[str, Any] = {
            "metadata": {
                "repo": repo,
                "pr_number": pr_number,
                "deterministic_commit_sha": commit_sha,
                "deterministic_merged_at": merged_at_timestamp,
            },
            "status": "FAILED" if pipeline_error else "SUCCESS",
            "pipeline_error": pipeline_error,
            "llm_input": {
                "diff_text": diff_text,
            },
            "llm_output": llm_raw_output,
            "final_simplex": resolved_simplex.model_dump() if resolved_simplex else None,
        }

        try:
            with log_file.open("w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=2, ensure_ascii=False)
            logger.info("Audit log written to %s", log_file)
        except Exception as e:
            logger.error("Failed to write audit log for PR %d: %s", pr_number, e)
            
        return log_file
