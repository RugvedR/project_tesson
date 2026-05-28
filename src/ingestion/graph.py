"""
Tesson LangGraph State Machine — Task 1.5

This is the orchestrator that wires all Phase 1 components together.
The pipeline flows:

  [ingest] → [hydrate] → [extract_llm] ─→ [resolve_terl] → [write_ledger] → END
                              ↑ retry        (max 3 retries on Pydantic failure)

Design Principles:
  - The LLM has zero free-form output. `.with_structured_output(TessonSimplex)`
    forces a Pydantic-validated response or raises immediately.
  - If the LLM fails Pydantic validation, the error message is fed back into
    the prompt as self-correction context (max 3 retries).
  - The TERL resolution happens AFTER the LLM — the LLM picks from the known
    entity list, and TERL double-checks and corrects any drift.
  - The graph is compiled once at module load time and reused across all calls.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from typing_extensions import TypedDict

from ingestion.hydrator import hydrate_pr_diff
from ingestion.prompt import SYSTEM_PROMPT, build_user_prompt
from ingestion.schemas import TessonSimplex, TessonSimplexExtraction
from ingestion.terl import EntityResolutionLedger
from ledger import store

load_dotenv()

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


# ─── LLM Factory ───────────────────────────────────────────────────────────────────

def _build_llm():
    """Instantiate the LLM based on LLM_PROVIDER env var.

    Supported providers:
      groq   — Free tier. Set GROQ_API_KEY. Model: GROQ_MODEL (default: llama-3.3-70b-versatile)
      openai — Paid.      Set OPENAI_API_KEY. Model: OPENAI_MODEL (default: gpt-4o)
      gemini — Free.      Set GEMINI_API_KEY. Model: GEMINI_MODEL (default: gemini-1.5-flash)
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "groq":
        from langchain_groq import ChatGroq
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        logger.info("Using Groq LLM: %s", model)
        return ChatGroq(model=model, temperature=0)

    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        logger.info("Using OpenAI LLM: %s", model)
        return ChatOpenAI(model=model, temperature=0)

    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        logger.info("Using Gemini LLM: %s", model)
        if "GEMINI_API_KEY" in os.environ and "GOOGLE_API_KEY" not in os.environ:
            os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
        return ChatGoogleGenerativeAI(model=model, temperature=0)

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER='{provider}'. Must be 'groq', 'openai', or 'gemini'."
        )


# ─── Pipeline State ───────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    """The shared state object that flows through the graph."""
    # Input
    raw_payload: dict[str, Any]      # The raw webhook JSON from GitHub
    repo: str                         # e.g. "owner/repo"
    pr_number: int                    # PR number

    # Hydration
    diff_text: Optional[str]          # Raw git diff from GitHub API
    commit_sha: Optional[str]         # Merge commit SHA
    merged_at_timestamp: Optional[int] # Merge timestamp

    # LLM extraction
    llm_raw_output: Optional[dict]    # Raw LLM response before TERL
    llm_error: Optional[str]          # Pydantic error text (fed back for retry)
    retries: int                      # Number of LLM retry attempts made

    # TERL resolution
    resolved_simplex: Optional[TessonSimplex]  # Final validated simplex

    # Control
    pipeline_error: Optional[str]     # Fatal error (stops pipeline)


# ─── Node: Ingest ─────────────────────────────────────────────────────────────

def ingest_node(state: PipelineState) -> PipelineState:
    """Validate the raw webhook payload and extract essential fields.

    Ensures we have a repo name and PR number before doing any expensive work.
    """
    payload = state.get("raw_payload", {})

    repo = state.get("repo") or (
        payload.get("repository", {}).get("full_name")
    )
    pr_number = state.get("pr_number") or (
        payload.get("pull_request", {}).get("number")
    )

    if not repo:
        return {**state, "pipeline_error": "Cannot determine repository name from webhook payload."}
    if not pr_number:
        return {**state, "pipeline_error": "Cannot determine PR number from webhook payload."}

    logger.info("[ingest] Accepted PR #%d from %s", pr_number, repo)
    return {**state, "repo": repo, "pr_number": pr_number, "pipeline_error": None}


# ─── Node: Hydrate ────────────────────────────────────────────────────────────

def hydrate_node(state: PipelineState) -> PipelineState:
    """Download the raw git diff for the PR from GitHub."""
    if state.get("pipeline_error"):
        return state  # skip if already failed

    repo = state["repo"]
    pr_number = state["pr_number"]

    try:
        hydrated = hydrate_pr_diff(repo_full_name=repo, pr_number=pr_number)
        diff_text = hydrated["diff_text"]
        logger.info("[hydrate] Got diff for PR #%d (%d chars)", pr_number, len(diff_text))
        return {
            **state,
            "diff_text": diff_text,
            "commit_sha": hydrated["commit_sha"],
            "merged_at_timestamp": hydrated["merged_at_timestamp"],
        }
    except Exception as e:
        logger.error("[hydrate] Failed to hydrate PR #%d: %s", pr_number, e)
        return {**state, "pipeline_error": f"Hydration failed: {e}"}


# ─── Node: Extract (LLM) ─────────────────────────────────────────────────────

def extract_llm_node(state: PipelineState, terl: EntityResolutionLedger, llm) -> PipelineState:
    """Send the diff to the LLM and extract a structured TessonSimplexExtraction.

    Uses `.with_structured_output(TessonSimplexExtraction)` to enforce schema at LLM
    response time. On failure, feeds the error back for self-correction.
    """
    if state.get("pipeline_error"):
        return state

    diff_text = state.get("diff_text", "")
    pr_number = state.get("pr_number")
    repo = state.get("repo")
    retries = state.get("retries", 0)
    llm_error = state.get("llm_error")

    if retries >= MAX_RETRIES:
        return {
            **state,
            "pipeline_error": f"LLM extraction failed after {MAX_RETRIES} retries.",
        }

    known_entities = terl.get_all_canonical_ids()
    user_prompt = build_user_prompt(
        diff_text=diff_text,
        known_entities=known_entities,
        pr_number=pr_number,
        repo=repo,
    )

    # If we're retrying, inject the previous error as correction context
    if llm_error:
        user_prompt += (
            f"\n\n[CORRECTION REQUEST — Attempt {retries + 1}/{MAX_RETRIES}]\n"
            f"Your previous response failed Pydantic validation:\n{llm_error}\n"
            f"Please fix the above errors and respond with a valid TessonSimplexExtraction."
        )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    try:
        structured_llm = llm.with_structured_output(TessonSimplexExtraction)
        result: TessonSimplexExtraction = structured_llm.invoke(messages)
        logger.info(
            "[extract_llm] PR #%d → extracted nodes: %s",
            pr_number, result.nodes,
        )
        return {
            **state,
            "llm_raw_output": result.model_dump(),
            "llm_error": None,
            "retries": retries,
        }
    except ValidationError as e:
        logger.warning(
            "[extract_llm] Pydantic validation failed (attempt %d/%d): %s",
            retries + 1, MAX_RETRIES, e,
        )
        return {
            **state,
            "llm_error": str(e),
            "retries": retries + 1,
        }
    except Exception as e:
        logger.error("[extract_llm] Unexpected error: %s", e)
        return {**state, "pipeline_error": f"LLM call failed: {e}"}


# ─── Node: Resolve TERL ──────────────────────────────────────────────────────

def resolve_terl_node(state: PipelineState, terl: EntityResolutionLedger) -> PipelineState:
    """Run every node ID from the LLM output through the TERL.

    Even though the LLM was given the known entity list, it may still drift
    slightly. The TERL is the final authority — it collapses any remaining
    naming variants to the canonical Matrix Row ID.
    """
    if state.get("pipeline_error"):
        return state

    raw_output = state.get("llm_raw_output")
    if not raw_output:
        return {**state, "pipeline_error": "No LLM output to resolve."}

    pr_number = state.get("pr_number")

    try:
        raw_nodes: list[str] = raw_output.get("nodes", [])
        resolved_nodes = [terl.resolve_entity(node_id) for node_id in raw_nodes]

        # Rebuild simplex with resolved nodes
        simplex_data = {**raw_output, "nodes": resolved_nodes}
        if pr_number:
            simplex_data["pr_number"] = pr_number

        if state.get("commit_sha"):
            simplex_data["commit_sha"] = state["commit_sha"]
        if state.get("merged_at_timestamp"):
            simplex_data["timestamp"] = state["merged_at_timestamp"]

        resolved = TessonSimplex.model_validate(simplex_data)
        logger.info(
            "[resolve_terl] PR #%d resolved nodes: %s → %s (simplex_id: %s)",
            pr_number, raw_nodes, resolved_nodes, resolved.simplex_id,
        )
        return {**state, "resolved_simplex": resolved}

    except ValidationError as e:
        return {**state, "pipeline_error": f"TERL resolution produced invalid simplex: {e}"}
    except Exception as e:
        return {**state, "pipeline_error": f"TERL resolution failed: {e}"}


# ─── Node: Write Ledger ──────────────────────────────────────────────────────

def write_ledger_node(state: PipelineState) -> PipelineState:
    """Append the resolved simplex to the immutable JSONL ledger."""
    if state.get("pipeline_error"):
        return state

    simplex = state.get("resolved_simplex")
    if not simplex:
        return {**state, "pipeline_error": "No resolved simplex to write."}

    try:
        store.append(simplex)
        logger.info(
            "[write_ledger] Wrote simplex %s for PR #%d",
            simplex.simplex_id, state.get("pr_number"),
        )
        return state
    except Exception as e:
        return {**state, "pipeline_error": f"Ledger write failed: {e}"}


# ─── Routing Logic ────────────────────────────────────────────────────────────

def should_retry_or_resolve(state: PipelineState) -> str:
    """Router: after LLM extraction, decide whether to retry or proceed."""
    if state.get("pipeline_error"):
        return "end"
    if state.get("llm_error") and state.get("retries", 0) < MAX_RETRIES:
        return "retry"
    if state.get("llm_raw_output") is None:
        return "retry"
    return "resolve"


# ─── Graph Factory ────────────────────────────────────────────────────────────

def build_pipeline(
    terl: Optional[EntityResolutionLedger] = None,
    llm=None,
) -> CompiledStateGraph:
    """Build and compile the LangGraph pipeline.

    Args:
        terl: EntityResolutionLedger instance (creates default if None).
        llm: LangChain LLM instance (creates gpt-4o if None).

    Returns:
        Compiled LangGraph StateGraph ready to invoke.
    """
    if terl is None:
        terl_path = os.getenv("TERL_PATH", "./data/terl_ledger.json")
        terl = EntityResolutionLedger(ledger_path=terl_path)

    if llm is None:
        llm = _build_llm()

    # Bind terl and llm into node closures
    def _extract(state): return extract_llm_node(state, terl=terl, llm=llm)
    def _resolve(state): return resolve_terl_node(state, terl=terl)

    graph = StateGraph(PipelineState)

    # Register nodes
    graph.add_node("ingest", ingest_node)
    graph.add_node("hydrate", hydrate_node)
    graph.add_node("extract_llm", _extract)
    graph.add_node("resolve_terl", _resolve)
    graph.add_node("write_ledger", write_ledger_node)

    # Linear edges
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "hydrate")
    graph.add_edge("hydrate", "extract_llm")

    # Conditional edge: retry loop or proceed to TERL
    graph.add_conditional_edges(
        "extract_llm",
        should_retry_or_resolve,
        {
            "retry": "extract_llm",    # Loop back for self-correction
            "resolve": "resolve_terl",
            "end": END,
        },
    )

    graph.add_edge("resolve_terl", "write_ledger")
    graph.add_edge("write_ledger", END)

    return graph.compile()


# ─── Module-Level Pipeline (singleton for API use) ───────────────────────────

# Lazily initialized — only instantiated when first used
_pipeline = None


def get_pipeline():
    """Get or create the module-level compiled pipeline."""
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()
    return _pipeline


def run_pipeline(repo: str, pr_number: int, raw_payload: Optional[dict] = None) -> PipelineState:
    """Convenience function to run the pipeline for a single PR.

    Args:
        repo: Full repo name (e.g. "owner/repo").
        pr_number: PR number.
        raw_payload: Optional raw webhook payload.

    Returns:
        Final PipelineState. Check state['pipeline_error'] for failures
        and state['resolved_simplex'] for the result.
    """
    pipeline = get_pipeline()
    initial_state: PipelineState = {
        "raw_payload": raw_payload or {},
        "repo": repo,
        "pr_number": pr_number,
        "diff_text": None,
        "commit_sha": None,
        "merged_at_timestamp": None,
        "llm_raw_output": None,
        "llm_error": None,
        "retries": 0,
        "resolved_simplex": None,
        "pipeline_error": None,
    }
    return pipeline.invoke(initial_state)
