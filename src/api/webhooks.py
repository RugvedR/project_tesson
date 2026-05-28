"""
Tesson Webhook API — Task 1.2

FastAPI application exposing the ingestion pipeline via HTTP endpoints.
Receives GitHub PR webhooks, validates HMAC-SHA256 signatures, and routes
valid events into the LangGraph pipeline via an async queue.

Endpoints:
  POST /webhooks/github   — GitHub PR events (HMAC verified)
  POST /webhooks/jira     — Jira issue events (stub for Phase 1)
  GET  /health            — Liveness probe
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

load_dotenv()

logger = logging.getLogger(__name__)

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Tesson Ingestion API",
    description="Zero-Instrumentation RCA — Webhook ingestion layer",
    version="0.1.0",
)

# In-memory async queue (Phase 1: no Redis/Celery dependency)
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


# ─── Signature Verification ───────────────────────────────────────────────────

def _verify_github_signature(body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub HMAC-SHA256 webhook signature.

    GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>
    We compute:   HMAC(secret, body, SHA256) and compare.

    Returns True if signatures match or if no secret is configured (dev mode).
    """
    if not GITHUB_WEBHOOK_SECRET:
        logger.warning(
            "GITHUB_WEBHOOK_SECRET not set — skipping signature verification (dev mode)."
        )
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_sig, signature_header)


# ─── Background Processing ────────────────────────────────────────────────────

async def _process_github_event(payload: dict[str, Any]) -> None:
    """Background task: route a GitHub webhook payload into the pipeline.

    Only processes 'closed' + merged PRs. All other events are acknowledged
    and silently dropped (we don't care about PR comments, reviews, etc.)
    """
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    merged = pr.get("merged", False)
    pr_number = pr.get("number")
    repo = payload.get("repository", {}).get("full_name")

    if action != "closed" or not merged:
        logger.debug(
            "Skipping GitHub event: action=%s merged=%s (not a merged PR)", action, merged
        )
        return

    if not pr_number or not repo:
        logger.warning("Malformed PR payload: missing pr_number or repo name.")
        return

    logger.info("Queuing PR #%d from %s for pipeline processing.", pr_number, repo)

    try:
        # Import here to avoid circular imports at module load time
        from ingestion.graph import run_pipeline
        final_state = run_pipeline(repo=repo, pr_number=pr_number, raw_payload=payload)

        if final_state.get("pipeline_error"):
            logger.error(
                "Pipeline error for PR #%d: %s",
                pr_number, final_state["pipeline_error"],
            )
        else:
            logger.info(
                "Pipeline SUCCESS for PR #%d — simplex: %s",
                pr_number,
                final_state.get("resolved_simplex"),
            )
    except Exception as e:
        logger.exception("Unhandled exception processing PR #%d: %s", pr_number, e)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness probe — returns 200 OK when the service is up."""
    return {"status": "ok", "service": "tesson-ingestion-api", "version": "0.1.0"}


@app.post("/webhooks/github", tags=["webhooks"], status_code=202)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive a GitHub webhook event.

    Validates the HMAC-SHA256 signature, then dispatches valid PR merge events
    to the LangGraph pipeline as a background task.

    Returns 202 Accepted immediately — processing is asynchronous.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not _verify_github_signature(body, signature):
        logger.warning("GitHub webhook signature verification FAILED.")
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    logger.info("Received GitHub event: %s", event_type)

    if event_type == "pull_request":
        background_tasks.add_task(_process_github_event, payload)

    return {"accepted": True, "event": event_type}


@app.post("/webhooks/jira", tags=["webhooks"], status_code=202)
async def jira_webhook(request: Request) -> dict:
    """Jira webhook stub — Phase 1 placeholder.

    Accepts and logs Jira issue events. Full processing is Phase 2.
    """
    try:
        payload: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event = payload.get("webhookEvent", "unknown")
    logger.info("Received Jira event: %s (stub — not processed in Phase 1)", event)
    return {"accepted": True, "event": event, "note": "Jira processing is Phase 2."}
