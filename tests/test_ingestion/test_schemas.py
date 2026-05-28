"""
Stage 2 Verify — Unit tests for TessonNode and TessonSimplex schemas.

Tests are grouped into:
  - Valid construction (must not raise)
  - ID format enforcement (must raise ValidationError)
  - Node count enforcement (must raise ValidationError)
  - Duplicate node detection (must raise ValidationError)
  - Metadata type enforcement (must raise ValidationError)
  - Commit SHA validation
"""

import pytest
from pydantic import ValidationError

from ingestion.schemas import EntityType, TessonNode, TessonSimplex


# ─── TessonNode Tests ────────────────────────────────────────────────────────

class TestTessonNodeValid:
    def test_minimal_valid_node(self):
        node = TessonNode(id="redis-cache", entity_type=EntityType.CACHE, source_name="redis-cart")
        assert node.id == "redis-cache"
        assert node.entity_type == EntityType.CACHE

    def test_underscore_id(self):
        node = TessonNode(id="postgres_db", entity_type=EntityType.DB, source_name="PostgresDB")
        assert node.id == "postgres_db"

    def test_alphanumeric_id(self):
        node = TessonNode(id="service01", entity_type=EntityType.SERVICE, source_name="service01")
        assert node.id == "service01"

    def test_all_entity_types(self):
        for etype in EntityType:
            node = TessonNode(id="test-node", entity_type=etype, source_name="raw")
            assert node.entity_type == etype

    def test_node_is_immutable(self):
        node = TessonNode(id="redis-cache", entity_type=EntityType.CACHE, source_name="r")
        with pytest.raises(Exception):  # frozen model
            node.id = "new-id"


class TestTessonNodeInvalidIds:
    def test_uppercase_rejected(self):
        with pytest.raises(ValidationError, match="Invalid Tesson ID"):
            TessonNode(id="Redis-Cache", entity_type=EntityType.CACHE, source_name="r")

    def test_spaces_rejected(self):
        with pytest.raises(ValidationError, match="Invalid Tesson ID"):
            TessonNode(id="my service", entity_type=EntityType.SERVICE, source_name="r")

    def test_special_chars_rejected(self):
        with pytest.raises(ValidationError, match="Invalid Tesson ID"):
            TessonNode(id="auth@db!", entity_type=EntityType.DB, source_name="r")

    def test_leading_hyphen_rejected(self):
        with pytest.raises(ValidationError, match="Invalid Tesson ID"):
            TessonNode(id="-badstart", entity_type=EntityType.SERVICE, source_name="r")

    def test_single_char_rejected(self):
        # Pydantic's min_length check fires before our regex validator
        with pytest.raises(ValidationError):
            TessonNode(id="a", entity_type=EntityType.SERVICE, source_name="r")

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            TessonNode(id="", entity_type=EntityType.SERVICE, source_name="r")

    def test_slash_rejected(self):
        with pytest.raises(ValidationError, match="Invalid Tesson ID"):
            TessonNode(id="some/path", entity_type=EntityType.SERVICE, source_name="r")


class TestTessonNodeSourceName:
    def test_newline_in_source_rejected(self):
        with pytest.raises(ValidationError, match="newline"):
            TessonNode(id="valid-id", entity_type=EntityType.SERVICE, source_name="has\nnewline")


# ─── TessonSimplex Tests ─────────────────────────────────────────────────────

class TestTessonSimplexValid:
    def test_minimal_simplex(self):
        s = TessonSimplex(nodes=["service-a", "service-b"], timestamp=1700000000)
        assert len(s.nodes) == 2
        assert s.timestamp == 1700000000
        assert s.simplex_id  # auto-generated UUID

    def test_three_node_simplex(self):
        s = TessonSimplex(nodes=["node-a", "node-b", "node-c"])
        assert len(s.nodes) == 3

    def test_auto_timestamp(self):
        import time
        before = int(time.time())
        s = TessonSimplex(nodes=["a1", "b1"])
        assert s.timestamp >= before

    def test_with_pr_number(self):
        s = TessonSimplex(nodes=["svc-a", "db-b"], pr_number=42, commit_sha="abc1234")
        assert s.pr_number == 42
        assert s.commit_sha == "abc1234"

    def test_with_metadata(self):
        s = TessonSimplex(nodes=["a1", "b1"], metadata={"repo": "test-repo", "env": "prod"})
        assert s.metadata["repo"] == "test-repo"

    def test_commit_sha_normalized_lowercase(self):
        s = TessonSimplex(nodes=["a1", "b1"], commit_sha="ABCDEF1234567")
        assert s.commit_sha == "abcdef1234567"


class TestTessonSimplexInvalid:
    def test_single_node_rejected(self):
        with pytest.raises(ValidationError, match="at least 2 nodes"):
            TessonSimplex(nodes=["only-one"])

    def test_empty_nodes_rejected(self):
        with pytest.raises(ValidationError):
            TessonSimplex(nodes=[])

    def test_duplicate_nodes_rejected(self):
        with pytest.raises(ValidationError, match="duplicates"):
            TessonSimplex(nodes=["service-a", "service-a"])

    def test_invalid_node_id_in_list(self):
        with pytest.raises(ValidationError, match="Invalid Tesson ID"):
            TessonSimplex(nodes=["valid-id", "INVALID UPPERCASE"])

    def test_non_string_metadata_value_rejected(self):
        # Pydantic's type coercion catches int values before our validator
        with pytest.raises(ValidationError):
            TessonSimplex(nodes=["a1", "b1"], metadata={"key": 123})

    def test_negative_pr_number_rejected(self):
        with pytest.raises(ValidationError):
            TessonSimplex(nodes=["a1", "b1"], pr_number=-1)

    def test_invalid_commit_sha_rejected(self):
        with pytest.raises(ValidationError, match="commit SHA"):
            TessonSimplex(nodes=["a1", "b1"], commit_sha="not-a-sha!!!")

    def test_short_commit_sha_rejected(self):
        with pytest.raises(ValidationError, match="commit SHA"):
            TessonSimplex(nodes=["a1", "b1"], commit_sha="abc12")  # only 5 chars, need 7
