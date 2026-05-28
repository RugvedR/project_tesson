"""
Tesson Ingestion Pipeline — Universal Data Contract (Task 1.1)

These Pydantic v2 models are the absolute bedrock of the system.
The LLM is bound to produce ONLY these shapes. No free text, no nested objects,
no raw descriptions. Everything is an ID, an Enum, or a primitive scalar.

Design Constraints (from spec):
  - node IDs: lowercase alphanumeric + underscores/hyphens only (no spaces)
  - nodes list: minimum 2 entries (a simplex requires at least 2 vertices)
  - timestamp: Unix epoch integer only — no datetime objects
  - metadata: flat string-to-string dict only — no nested structures
"""

from __future__ import annotations

import re
import time
import uuid
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

# ─── Enums ───────────────────────────────────────────────────────────────────

class EntityType(str, Enum):
    """Exhaustive list of entity types Tesson can model.

    Adding a new type here requires a corresponding TERL seed entry.
    Do NOT create catch-all types like 'other' — force the LLM to pick the
    closest valid type.
    """
    COMMIT   = "commit"
    SERVICE  = "service"
    DB       = "db"
    QUEUE    = "queue"
    CACHE    = "cache"
    ALERT    = "alert"
    API      = "api"
    WORKER   = "worker"
    GATEWAY  = "gateway"
    STORAGE  = "storage"


# ─── ID Validation ───────────────────────────────────────────────────────────

_VALID_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,127}$")


def _validate_tesson_id(v: str) -> str:
    """Enforce the Tesson ID contract: lowercase, alphanumeric + _ or -.
    No spaces. No uppercase. No special characters. Length 2–128.
    """
    if not _VALID_ID_RE.match(v):
        raise ValueError(
            f"Invalid Tesson ID '{v}'. Must be lowercase alphanumeric with "
            "underscores or hyphens only, 2–128 chars, no spaces."
        )
    return v


# ─── Models ──────────────────────────────────────────────────────────────────

class TessonNode(BaseModel):
    """A single vertex in the Tesson topological complex.

    Represents exactly one infrastructure entity (a service, a database,
    a cache, etc.) at a specific point in time. The 'id' field IS the
    Matrix Row ID — it never contains human-readable descriptions.
    """

    model_config = {"frozen": True}  # Nodes are immutable once created

    id: Annotated[str, Field(
        description=(
            "TERL-resolved Matrix Row ID. Strict format: lowercase alphanumeric "
            "+ underscores/hyphens. This must be the official ID from the TERL, "
            "not a raw entity name from the PR."
        ),
        min_length=2,
        max_length=128,
    )]

    entity_type: EntityType = Field(
        description="The category of this infrastructure entity."
    )

    source_name: str = Field(
        description=(
            "The raw name as it appeared in the git diff BEFORE TERL resolution. "
            "Audit trail only — never used in matrix math."
        ),
        min_length=1,
        max_length=256,
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_tesson_id(v)

    @field_validator("source_name")
    @classmethod
    def no_newlines_in_source(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("source_name must not contain newline characters.")
        return v.strip()


class TessonSimplexExtraction(BaseModel):
    """The target schema for LLM extraction.
    
    This schema is used strictly to enforce the LLM to only output the semantic
    components (nodes and metadata) of a simplex. Mechanical fields like timestamps
    and commit SHAs are injected deterministically by the code pipeline.
    """
    nodes: list[str] = Field(
        description=(
            "List of infrastructure entities involved in this interaction. "
            "Minimum 2 nodes required. IDs should be as close to the expected TERL names as possible."
        ),
        min_length=2,
    )

    metadata: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Flat key-value store for semantic context extracted from the PR. "
            "Keys must be lowercase alphanumeric with underscores. Values MUST be plain strings."
        ),
    )


class TessonSimplex(BaseModel):
    """A geometric interaction (edge, triangle, or higher simplex) between nodes.

    Each TessonSimplex represents a real-world event (PR merge, alert firing)
    that links two or more infrastructure entities. This is the atomic unit
    that gets appended to the immutable ledger and compiled into the sparse
    matrix.

    Schema Rules (hard constraints the LLM must follow):
      - 'nodes' must contain >= 2 entries (a simplex requires at least 2 vertices)
      - All entries in 'nodes' must be valid Tesson IDs (TERL-resolved)
      - 'metadata' values must be plain strings — no nested JSON
      - 'timestamp' must be a Unix epoch integer
    """

    simplex_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique UUID for this geometric interaction event.",
    )

    nodes: list[str] = Field(
        description=(
            "List of TERL-resolved Matrix Row IDs involved in this interaction. "
            "Minimum 2 nodes required. All IDs must conform to the Tesson ID format."
        ),
        min_length=2,
    )

    timestamp: int = Field(
        default_factory=lambda: int(time.time()),
        description="Unix epoch timestamp (integer seconds). No datetime objects.",
        ge=0,
    )

    pr_number: int | None = Field(
        default=None,
        description="GitHub PR number that generated this simplex, if applicable.",
        ge=1,
    )

    commit_sha: str | None = Field(
        default=None,
        description="The merge commit SHA from GitHub, if applicable.",
        max_length=64,
    )

    metadata: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Flat key-value store for additional context. "
            "Values MUST be plain strings — no nested objects or lists."
        ),
    )

    @field_validator("simplex_id")
    @classmethod
    def validate_uuid4(cls, v: str) -> str:
        try:
            val = uuid.UUID(v, version=4)
        except ValueError:
            raise ValueError(f"Invalid simplex_id '{v}': must be a valid UUIDv4 string.") from None
        return str(val)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: int) -> int:
        now = int(time.time())
        if v > now + 300:
            raise ValueError(
                f"Timestamp {v} is in the future. "
                "Tesson simplices must represent historical events."
            )
        return v

    @field_validator("nodes", mode="before")
    @classmethod
    def validate_nodes(cls, v: list) -> list:
        if len(v) < 2:
            raise ValueError(
                f"TessonSimplex requires at least 2 nodes, got {len(v)}. "
                "A simplex cannot exist with a single vertex."
            )
        validated = []
        for node_id in v:
            validated.append(_validate_tesson_id(str(node_id)))
        # Lexicographically sort nodes for canonical simplex representation
        return sorted(validated)

    @field_validator("commit_sha")
    @classmethod
    def validate_sha(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip().lower()
            if not re.match(r"^[0-9a-f]{7,40}$", clean):
                raise ValueError(
                    f"Invalid commit SHA '{v}'. Must be a hex string of 7–40 chars."
                )
            return clean
        return v

    @field_validator("metadata")
    @classmethod
    def validate_metadata_values(cls, v: dict) -> dict:
        for key, val in v.items():
            if not re.match(r"^[a-z0-9_]+$", key):
                raise ValueError(
                    f"Invalid metadata key '{key}'. "
                    "Keys must be lowercase alphanumeric and underscores only."
                )
            if not isinstance(val, str):
                raise ValueError(
                    f"metadata['{key}'] must be a plain string, got {type(val).__name__}. "
                    "No nested objects allowed in metadata."
                )
        return v

    @model_validator(mode="after")
    def no_duplicate_nodes(self) -> TessonSimplex:
        if len(self.nodes) != len(set(self.nodes)):
            seen = set()
            dupes = [n for n in self.nodes if n in seen or seen.add(n)]
            raise ValueError(
                f"TessonSimplex.nodes contains duplicates: {dupes}. "
                "Each node may appear only once per simplex."
            )
        return self
