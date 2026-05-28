"""
Tesson Entity Resolution Ledger (TERL) — Task 1.4

The TERL is the anti-hallucination firewall. Every string the LLM produces
is intercepted here before it can touch the matrix. The TERL collapses
naming chaos (e.g., "redis-cart", "RedisCartCache", "redis_cart_service")
into a single, canonical Matrix Row ID.

Algorithm:
  1. Normalize the input string (lowercase, strip, replace separators).
  2. Query all known entity names with RapidFuzz WRatio scorer.
  3. If best_score >= SIMILARITY_THRESHOLD (92%): return the official ID.
  4. If best_score < threshold: generate new UUID, persist to ledger, return.

The TERL is loaded from a JSON file on startup and written back on mutation.
It ships with a seed dictionary of common microservice infrastructure entities.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# Similarity threshold (per spec §Task 1.4). 92% = very high confidence.
SIMILARITY_THRESHOLD = 92.0

# ─── Seed Dictionary ─────────────────────────────────────────────────────────
# Pre-populated canonical entities for common microservice infrastructure.
# Keys = canonical Matrix Row ID, Values = list of known aliases.
# The TERL normalizes all aliases → resolves to the canonical ID.

SEED_ENTITIES: dict[str, dict] = {
    # ── Caches ──────────────────────────────────────────────────────────────
    "redis_cache": {
        "entity_type": "cache",
        "aliases": [
            "redis", "redis-cache", "redis_cache", "rediscache",
            "redis-cart", "redis_cart", "rediscart", "RedisCartCache",
            "redis-session", "redis_session", "session-cache",
        ],
    },
    "memcached": {
        "entity_type": "cache",
        "aliases": ["memcached", "memcache", "mem-cache", "mem_cache"],
    },

    # ── Databases ────────────────────────────────────────────────────────────
    "postgres_db": {
        "entity_type": "db",
        "aliases": [
            "postgres", "postgresql", "postgres-db", "postgres_db",
            "postgresdb", "auth_db", "login-database", "AuthenticationDB",
            "auth-database", "user_db", "user-db",
        ],
    },
    "mongodb": {
        "entity_type": "db",
        "aliases": [
            "mongodb", "mongo", "mongo-db", "mongo_db",
            "product_db", "catalog-db", "catalog_db",
        ],
    },
    "mysql_db": {
        "entity_type": "db",
        "aliases": ["mysql", "mysql-db", "mysql_db", "mysqldb"],
    },

    # ── Message Queues ───────────────────────────────────────────────────────
    "kafka": {
        "entity_type": "queue",
        "aliases": [
            "kafka", "kafka-queue", "kafka_queue", "kafkaqueue",
            "event-bus", "event_bus", "eventbus", "message-queue",
        ],
    },
    "rabbitmq": {
        "entity_type": "queue",
        "aliases": [
            "rabbitmq", "rabbit-mq", "rabbit_mq", "rabbitMQ",
            "amqp", "task-queue", "task_queue",
        ],
    },

    # ── Services (Google Boutique microservices-demo) ────────────────────────
    "frontend_service": {
        "entity_type": "service",
        "aliases": [
            "frontend", "frontend-service", "frontend_service",
            "web-frontend", "web_frontend", "ui-service",
        ],
    },
    "cart_service": {
        "entity_type": "service",
        "aliases": [
            "cart", "cart-service", "cart_service", "cartservice",
            "shopping-cart", "shopping_cart",
        ],
    },
    "checkout_service": {
        "entity_type": "service",
        "aliases": [
            "checkout", "checkout-service", "checkout_service", "checkoutservice",
            "order-service", "order_service",
        ],
    },
    "payment_service": {
        "entity_type": "service",
        "aliases": [
            "payment", "payment-service", "payment_service", "paymentservice",
            "payment-api", "payment_api", "billing-service", "billing_service",
        ],
    },
    "product_catalog_service": {
        "entity_type": "service",
        "aliases": [
            "productcatalog", "product-catalog", "product_catalog",
            "productcatalogservice", "catalog-service", "catalog_service",
        ],
    },
    "recommendation_service": {
        "entity_type": "service",
        "aliases": [
            "recommendation", "recommendation-service", "recommendation_service",
            "recommendationservice", "recommender",
        ],
    },
    "shipping_service": {
        "entity_type": "service",
        "aliases": [
            "shipping", "shipping-service", "shipping_service", "shippingservice",
        ],
    },
    "email_service": {
        "entity_type": "service",
        "aliases": [
            "email", "email-service", "email_service", "emailservice",
            "notification-service", "notification_service",
        ],
    },
    "currency_service": {
        "entity_type": "service",
        "aliases": [
            "currency", "currency-service", "currency_service", "currencyservice",
            "exchange-service", "exchange_service",
        ],
    },
    "ad_service": {
        "entity_type": "service",
        "aliases": [
            "ad", "ad-service", "ad_service", "adservice",
            "ads-service", "ads_service", "advertisement",
        ],
    },
    "load_generator": {
        "entity_type": "service",
        "aliases": [
            "loadgenerator", "load-generator", "load_generator", "traffic-generator",
        ],
    },

    # ── API Gateways ─────────────────────────────────────────────────────────
    "api_gateway": {
        "entity_type": "gateway",
        "aliases": [
            "api-gateway", "api_gateway", "apigateway",
            "nginx", "nginx-gateway", "kong", "envoy",
        ],
    },

    # ── Storage ──────────────────────────────────────────────────────────────
    "gcs_storage": {
        "entity_type": "storage",
        "aliases": [
            "gcs", "google-cloud-storage", "cloud-storage",
            "s3", "aws-s3", "blob-storage",
        ],
    },
}


# ─── TERL Class ──────────────────────────────────────────────────────────────

class EntityResolutionLedger:
    """The Tesson Entity Resolution Ledger.

    Maintains a bidirectional mapping:
      alias_string → canonical_matrix_id

    The underlying data structure:
      {
        "canonical_id": {
          "entity_type": "service",
          "aliases": ["alias1", "alias2", ...]
        },
        ...
      }

    A flat alias_map is maintained in memory for O(1) lookup before fuzzy
    search, avoiding RapidFuzz overhead for exact-match cases.
    """

    def __init__(self, ledger_path: Optional[Path] = None) -> None:
        self.ledger_path = ledger_path
        # Main store: canonical_id → {entity_type, aliases}
        self._store: dict[str, dict] = {}
        # Fast lookup: normalized_alias → canonical_id
        self._alias_map: dict[str, str] = {}

        # Bootstrap with seed entities
        for canonical_id, data in SEED_ENTITIES.items():
            self._store[canonical_id] = {
                "entity_type": data["entity_type"],
                "aliases": list(data["aliases"]),
            }
            for alias in data["aliases"]:
                self._alias_map[self._normalize(alias)] = canonical_id

        # Load persisted ledger on top of seed (user data overrides)
        if self.ledger_path and Path(self.ledger_path).exists():
            self._load_from_disk()

    # ── Normalization ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(raw: str) -> str:
        """Normalize a raw entity string for comparison.

        Steps:
          1. Strip whitespace
          2. Lowercase
          3. Replace hyphens, dots, spaces with underscores
          4. Collapse multiple underscores
          5. Remove leading/trailing underscores
        """
        s = raw.strip().lower()
        s = re.sub(r"[\-\.\s]+", "_", s)
        s = re.sub(r"_+", "_", s)
        return s.strip("_")

    # ── Core Resolution ──────────────────────────────────────────────────────

    def resolve_entity(self, raw_string: str, entity_type: Optional[str] = None) -> str:
        """Resolve a raw entity string to its canonical Matrix Row ID.

        Algorithm:
          1. Normalize the input.
          2. Check exact alias map (O(1), free).
          3. Run RapidFuzz WRatio against all known aliases.
          4a. Score >= SIMILARITY_THRESHOLD → return canonical ID.
          4b. Score <  SIMILARITY_THRESHOLD → register new entity, return new ID.

        Args:
            raw_string: The raw entity name from the LLM (e.g., "redis-cart").
            entity_type: Optional hint for entity type when registering new nodes.

        Returns:
            The canonical Matrix Row ID (always a valid Tesson ID format).
        """
        normalized = self._normalize(raw_string)

        # ── Step 1: Exact match (fast path) ─────────────────────────────────
        if normalized in self._alias_map:
            canonical = self._alias_map[normalized]
            logger.debug("TERL exact match: '%s' → '%s'", raw_string, canonical)
            return canonical

        # ── Step 2: Fuzzy match via RapidFuzz ───────────────────────────────
        all_aliases = list(self._alias_map.keys())
        if all_aliases:
            result = process.extractOne(
                normalized,
                all_aliases,
                scorer=fuzz.WRatio,
                score_cutoff=SIMILARITY_THRESHOLD,
            )
            if result is not None:
                best_alias, best_score, _ = result
                canonical = self._alias_map[best_alias]
                logger.info(
                    "TERL fuzzy match: '%s' → '%s' (score=%.1f, via alias='%s')",
                    raw_string, canonical, best_score, best_alias,
                )
                # Register this alias so future exact matches are instant
                self._register_alias(canonical, normalized)
                return canonical

        # ── Step 3: New entity — register and return ─────────────────────────
        new_id = self._generate_new_id(normalized)
        inferred_type = entity_type or "service"
        logger.warning(
            "TERL: No match for '%s' (normalized='%s'). "
            "Registering as new entity '%s' (type=%s).",
            raw_string, normalized, new_id, inferred_type,
        )
        self._register_new_entity(new_id, normalized, inferred_type)
        return new_id

    def register_entity(self, canonical_id: str, aliases: list[str], entity_type: str) -> None:
        """Force-register a new canonical entity (used for bootstrapping / admin).

        Raises ValueError if canonical_id already exists.
        """
        if canonical_id in self._store:
            raise ValueError(
                f"Entity '{canonical_id}' already exists in the TERL. "
                "Use add_alias() to add new aliases to existing entities."
            )
        self._store[canonical_id] = {
            "entity_type": entity_type,
            "aliases": aliases,
        }
        for alias in aliases:
            self._alias_map[self._normalize(alias)] = canonical_id
        self._persist()
        logger.info("TERL: Registered new entity '%s' with %d aliases.", canonical_id, len(aliases))

    def add_alias(self, canonical_id: str, new_alias: str) -> None:
        """Add a new alias to an existing canonical entity."""
        if canonical_id not in self._store:
            raise ValueError(f"Entity '{canonical_id}' not found in TERL.")
        normalized = self._normalize(new_alias)
        self._store[canonical_id]["aliases"].append(new_alias)
        self._alias_map[normalized] = canonical_id
        self._persist()

    def get_all_canonical_ids(self) -> list[str]:
        """Return all known canonical Matrix Row IDs."""
        return list(self._store.keys())

    def get_entity_type(self, canonical_id: str) -> Optional[str]:
        """Return the entity type for a canonical ID, or None if not found."""
        entry = self._store.get(canonical_id)
        return entry["entity_type"] if entry else None

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _generate_new_id(self, normalized_name: str) -> str:
        """Generate a new canonical ID from the normalized name.

        Prefers the normalized name if it satisfies the Tesson ID contract,
        otherwise falls back to a UUID4. Appends a short suffix if the name
        conflicts with an existing ID.
        """
        candidate = normalized_name[:64]  # cap to avoid overly long IDs
        # Ensure it matches the Tesson ID regex
        if re.match(r"^[a-z0-9][a-z0-9_\-]{1,127}$", candidate) and candidate not in self._store:
            return candidate
        # Fallback to UUID
        return f"entity_{uuid.uuid4().hex[:8]}"

    def _register_new_entity(self, canonical_id: str, normalized_alias: str, entity_type: str) -> None:
        self._store[canonical_id] = {
            "entity_type": entity_type,
            "aliases": [normalized_alias],
        }
        self._alias_map[normalized_alias] = canonical_id
        self._persist()

    def _register_alias(self, canonical_id: str, normalized_alias: str) -> None:
        """Add a new alias to an existing entity without full persistence (fast path)."""
        if normalized_alias not in self._store[canonical_id]["aliases"]:
            self._store[canonical_id]["aliases"].append(normalized_alias)
        self._alias_map[normalized_alias] = canonical_id
        # Note: we debounce persistence here; full persist happens on new entity creation

    def _persist(self) -> None:
        """Write the current TERL state to disk (if a path is configured)."""
        if not self.ledger_path:
            return
        path = Path(self.ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self._store, f, indent=2, ensure_ascii=False)

    def _load_from_disk(self) -> None:
        """Load persisted TERL state and merge over the seed data."""
        path = Path(self.ledger_path)
        with path.open("r", encoding="utf-8") as f:
            persisted: dict = json.load(f)
        for canonical_id, data in persisted.items():
            # Persisted data takes priority over seed
            self._store[canonical_id] = data
            for alias in data.get("aliases", []):
                self._alias_map[self._normalize(alias)] = canonical_id
        logger.info("TERL: Loaded %d entities from %s.", len(persisted), path)

    def stats(self) -> dict:
        """Return summary statistics for logging/debugging."""
        return {
            "total_entities": len(self._store),
            "total_aliases": len(self._alias_map),
            "threshold": SIMILARITY_THRESHOLD,
        }
