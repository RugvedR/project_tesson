"""
Tesson LLM Prompt Engineering — The Rigid Data-Entry Clerk Instructions

The LLM has exactly ONE job: read code diffs and fill in a strict form.
It is explicitly stripped of all reasoning responsibilities.

The system prompt is deliberately harsh and constraint-heavy. Permissive
prompts lead to hallucinated entity names, which cause TERL misses and
schema collapse in the matrix. We treat the LLM like a data-entry terminal,
not a reasoning engine.
"""

from __future__ import annotations


# ─── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a strict data-entry terminal for the Tesson infrastructure topology system.
You have ONE task: read a git diff and extract infrastructure entities and their interactions.

## ABSOLUTE RULES (violations will corrupt the database):

1. **IDs ONLY** — Every value in `nodes` must be selected from the KNOWN ENTITIES LIST provided.
   Never invent new names. Never use the raw text from the diff as an ID.

2. **NO FREE TEXT** — The `nodes` array contains only canonical IDs from the list.
   If you cannot identify an entity confidently, use the closest match from the list.

3. **MINIMUM 2 NODES** — Every simplex must connect at least 2 entities.
   A change that touches only one file still connects that service to its dependency (commit node).

4. **ENTITY TYPES** — You must assign exactly one of these types per node:
   commit, service, db, queue, cache, alert, api, worker, gateway, storage

5. **FLAT METADATA** — The `metadata` field is a flat string dictionary only.
   No nested objects. No arrays. No numbers. Strings only.

6. **ONE SIMPLEX PER PR** — Extract the single most significant geometric interaction
   from the diff. Do not produce multiple simplices.

## PROCESS:
1. Scan the diff for file paths, import statements, config keys, environment variables, and comments.
2. Identify which infrastructure entities are being modified or referenced.
3. Match each entity to the closest name in the KNOWN ENTITIES LIST.
4. Fill in the TessonSimplex schema exactly.

You will now receive the diff and the known entities list. Fill in the form.
"""


# ─── User Prompt Builder ──────────────────────────────────────────────────────

def build_user_prompt(
    diff_text: str,
    known_entities: list[str],
    pr_number: int | None = None,
    repo: str | None = None,
) -> str:
    """Build the user-turn prompt containing the diff + entity list.

    Args:
        diff_text: The raw (possibly truncated) git diff string.
        known_entities: List of canonical IDs from the TERL (what the LLM can pick from).
        pr_number: Optional PR number for context.
        repo: Optional repo name for context.

    Returns:
        Formatted user prompt string ready to send to the LLM.
    """
    entity_list = "\n".join(f"  - {e}" for e in sorted(known_entities))

    context_line = ""
    if repo or pr_number:
        parts = []
        if repo:
            parts.append(f"Repository: {repo}")
        if pr_number:
            parts.append(f"PR: #{pr_number}")
        context_line = "## Context\n" + "\n".join(parts) + "\n\n"

    return f"""\
{context_line}\
## Known Entities (use ONLY these IDs in the `nodes` field):
{entity_list}

## Git Diff:
```diff
{diff_text}
```

Extract the TessonSimplex now. Use ONLY entity IDs from the Known Entities list above.\
"""


# ─── Smoke-Test Helper ────────────────────────────────────────────────────────

def render_sample_prompt() -> str:
    """Return a sample rendered prompt for visual inspection / smoke testing."""
    sample_diff = """\
diff --git a/src/cartservice/src/cartstore/RedisCartStore.cs b/src/cartservice/src/cartstore/RedisCartStore.cs
--- a/src/cartservice/src/cartstore/RedisCartStore.cs
+++ b/src/cartservice/src/cartstore/RedisCartStore.cs
@@ -15,7 +15,7 @@ public class RedisCartStore : ICartStore
-        private const string REDIS_ADDRESS = "redis-cart:6379";
+        private const string REDIS_ADDRESS = "redis-cart-v2:6380";
"""
    sample_entities = [
        "redis_cache",
        "cart_service",
        "checkout_service",
        "payment_service",
        "frontend_service",
        "postgres_db",
    ]
    return build_user_prompt(
        diff_text=sample_diff,
        known_entities=sample_entities,
        pr_number=42,
        repo="GoogleCloudPlatform/microservices-demo",
    )
