"""
Tesson API Alert Schemas — Phase 2 Component

Defines the Pydantic models for incoming alerts and outgoing RCA responses.
Acts as the Anti-Corruption Layer (ACL) at the API edge. The core mathematical
engine is pure and Pydantic-free; it receives raw types (lists, arrays, integers)
from these edge validators.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AlertEvent(BaseModel):
    """Incoming alert payload from external monitoring systems (e.g., Datadog, PagerDuty)."""

    alert_entities: list[str] = Field(
        ...,
        min_length=1,
        description="Entity IDs (canonical or provisional) that are currently experiencing issues."
    )
    severity: str = Field(
        default="critical",
        description="Alert severity level (e.g. 'critical', 'warning', 'info')."
    )
    source: str = Field(
        default="manual",
        description="Source system that generated the alert (e.g., 'datadog', 'pagerduty', 'manual')."
    )
    timestamp: int | None = Field(
        default=None,
        description="Unix epoch timestamp of when the alert was triggered."
    )


class RCAResultItem(BaseModel):
    """A single candidate root cause (PR / commit simplex)."""

    commit_sha: str | None = Field(
        default=None,
        description="The merge commit SHA that introduced the simplex, if applicable."
    )
    pr_number: int | None = Field(
        default=None,
        description="The GitHub PR number associated with this change, if applicable."
    )
    simplex_id: str = Field(
        ...,
        description="The unique identifier of the simplex."
    )
    score: float = Field(
        ...,
        description="The diffusion score of this simplex (higher = more likely root cause)."
    )
    involved_entities: list[str] = Field(
        ...,
        description="The canonical entities participating in this simplex."
    )


class RCAResponse(BaseModel):
    """Structured RCA response returned to the caller."""

    results: list[RCAResultItem] = Field(
        ...,
        description="Sorted list of candidate root causes, ranked by descending score."
    )
    path_used: str = Field(
        ...,
        description="The traversal path(s) used to resolve this RCA. Values: 'matrix', 'provisional_fast_path', or 'mixed'."
    )
    matrix_stats: dict = Field(
        ...,
        description="Metadata and statistics of the matrix state used for the calculation."
    )
