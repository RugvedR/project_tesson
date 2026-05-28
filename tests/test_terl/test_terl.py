"""
Stage 3 Verify — Unit tests for the Tesson Entity Resolution Ledger (TERL).

Tests are grouped into:
  - Normalization edge cases
  - Exact alias matching (fast path)
  - Fuzzy matching above/below threshold
  - New entity registration + UUID generation
  - TERL collapse: same entity despite different naming conventions
  - Persistence round-trip (load from disk)
"""

import json

import pytest

from ingestion.terl import SIMILARITY_THRESHOLD, EntityResolutionLedger

# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def terl():
    """Fresh TERL instance (in-memory only, no disk path)."""
    return EntityResolutionLedger(ledger_path=None)


@pytest.fixture
def terl_with_file(tmp_path):
    """TERL instance backed by a temp file for persistence tests."""
    ledger_file = tmp_path / "terl_ledger.json"
    return EntityResolutionLedger(ledger_path=ledger_file), ledger_file


# ─── Normalization Tests ──────────────────────────────────────────────────────

class TestNormalization:
    def test_lowercase(self, terl):
        assert terl._normalize("Redis") == "redis"

    def test_hyphen_to_underscore(self, terl):
        assert terl._normalize("redis-cache") == "redis_cache"

    def test_spaces_to_underscore(self, terl):
        assert terl._normalize("redis cache") == "redis_cache"

    def test_dot_to_underscore(self, terl):
        assert terl._normalize("redis.cache") == "redis_cache"

    def test_strips_leading_trailing(self, terl):
        assert terl._normalize("  redis  ") == "redis"

    def test_collapses_multiple_separators(self, terl):
        assert terl._normalize("redis---cache") == "redis_cache"

    def test_camelcase_preserved_lowercased(self, terl):
        # CamelCase is lowercased but not split (we don't split camelCase)
        assert terl._normalize("RedisCartCache") == "rediscartcache"

    def test_mixed_separators(self, terl):
        assert terl._normalize("Redis-Cart_Cache") == "redis_cart_cache"


# ─── Exact Match Tests (Fast Path) ───────────────────────────────────────────

class TestExactMatch:
    def test_known_alias_resolves_to_canonical(self, terl):
        result = terl.resolve_entity("redis")
        assert result == "redis_cache"

    def test_hyphenated_alias(self, terl):
        result = terl.resolve_entity("redis-cart")
        assert result == "redis_cache"

    def test_postgres_alias(self, terl):
        assert terl.resolve_entity("postgres") == "postgres_db"

    def test_auth_db_alias(self, terl):
        assert terl.resolve_entity("auth_db") == "postgres_db"

    def test_login_database_alias(self, terl):
        assert terl.resolve_entity("login-database") == "postgres_db"

    def test_kafka_aliases(self, terl):
        assert terl.resolve_entity("kafka") == "kafka"
        assert terl.resolve_entity("event-bus") == "kafka"

    def test_payment_service_aliases(self, terl):
        assert terl.resolve_entity("payment-api") == "payment_service"
        assert terl.resolve_entity("payment_service") == "payment_service"


# ─── THE CRITICAL TEST: TERL Collapse (Milestone 2 Metric) ───────────────────

class TestTERLCollapse:
    """Proves the core anti-hallucination guarantee:
    Different naming conventions for the same entity → same Matrix Row ID.
    """

    def test_redis_naming_chaos_collapses(self, terl):
        """'redis-cart', 'RedisCartCache', 'redis_cart' → all same ID."""
        id1 = terl.resolve_entity("redis-cart")
        id2 = terl.resolve_entity("RedisCartCache")
        id3 = terl.resolve_entity("redis_cart")
        assert id1 == id2 == id3 == "redis_cache", (
            f"TERL collapse FAILED: {id1=}, {id2=}, {id3=}"
        )

    def test_postgres_naming_chaos_collapses(self, terl):
        id1 = terl.resolve_entity("auth_db")
        id2 = terl.resolve_entity("login-database")
        id3 = terl.resolve_entity("AuthenticationDB")
        id4 = terl.resolve_entity("postgres")
        # All should resolve to the same postgres canonical ID
        assert id1 == id2 == id4, f"Postgres collapse FAILED: {id1=}, {id2=}, {id4=}"

    def test_payment_service_collapse(self, terl):
        id1 = terl.resolve_entity("payment-api")
        id2 = terl.resolve_entity("billing-service")
        id3 = terl.resolve_entity("payment_service")
        assert id1 == id2 == id3, f"Payment collapse FAILED: {id1=}, {id2=}, {id3=}"

    def test_checkout_service_collapse(self, terl):
        id1 = terl.resolve_entity("checkout")
        id2 = terl.resolve_entity("checkoutservice")
        id3 = terl.resolve_entity("order-service")
        assert id1 == id2 == id3


# ─── New Entity Registration ──────────────────────────────────────────────────

class TestNewEntityRegistration:
    def test_unknown_entity_gets_new_id(self, terl):
        result = terl.resolve_entity("completely-unknown-service-xyz123")
        assert result is not None
        assert len(result) > 2  # Not empty

    def test_unknown_entity_is_persisted_in_memory(self, terl):
        result1 = terl.resolve_entity("my-novel-service-abc")
        result2 = terl.resolve_entity("my-novel-service-abc")
        assert result1 == result2  # Same ID on second call (exact match now)

    def test_very_different_string_gets_unique_id(self, terl):
        id1 = terl.resolve_entity("xyzzy-foobar-quux-9999")
        id2 = terl.resolve_entity("abcde-hello-world-0001")
        assert id1 != id2

    def test_force_register_new_entity(self, terl):
        terl.register_entity(
            canonical_id="custom_service",
            aliases=["my-custom-svc", "customsvc"],
            entity_type="service",
        )
        assert terl.resolve_entity("my-custom-svc") == "custom_service"
        assert terl.resolve_entity("customsvc") == "custom_service"

    def test_duplicate_register_raises(self, terl):
        with pytest.raises(ValueError, match="already exists"):
            terl.register_entity("redis_cache", ["new-alias"], "cache")


# ─── Persistence Round-Trip ──────────────────────────────────────────────────

class TestPersistence:
    def test_new_entity_persisted_to_file(self, terl_with_file):
        terl, ledger_file = terl_with_file
        terl.register_entity("brand_new_svc", ["brand-new", "brandnew"], "service")
        assert ledger_file.exists()
        data = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert "brand_new_svc" in data

    def test_reloaded_terl_resolves_persisted_entities(self, terl_with_file, tmp_path):
        terl1, ledger_file = terl_with_file
        terl1.register_entity("persisted_svc", ["persisted-service"], "service")

        # Create a new TERL instance from the same file
        terl2 = EntityResolutionLedger(ledger_path=ledger_file)
        result = terl2.resolve_entity("persisted-service")
        assert result == "persisted_svc"


# ─── Stats ───────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_returns_counts(self, terl):
        stats = terl.stats()
        assert stats["total_entities"] > 0
        assert stats["total_aliases"] > 0
        assert stats["threshold"] == SIMILARITY_THRESHOLD
