"""
Tesson Context Hydration Engine — Task 1.3

The "Absolute Truth Protocol": when a GitHub webhook fires, this module
discards the developer's PR description and instead fetches the raw git diff
(the actual code that changed). The LLM is forced to reason from code truth,
not human commentary.

Key Design Decisions:
  - Authentication via PyGithub with GITHUB_TOKEN from environment.
  - Truncation is file-header-aware: we always preserve "diff --git" lines so
    the LLM knows which files changed even in truncated diffs.
  - Token budget: 50,000 tokens (conservative; well within gpt-4o's 128k window
    but leaving room for system prompt + structured output schema).
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from github import Auth, Github, GithubException

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# Conservative token budget. Assumes ~4 chars/token.
MAX_TOKENS = int(os.getenv("HYDRATOR_MAX_TOKENS", "50000"))
CHARS_PER_TOKEN = 4
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN  # 200,000 characters


# ─── Public API ──────────────────────────────────────────────────────────────

def hydrate_pr_diff(
    repo_full_name: str,
    pr_number: int,
    github_token: str | None = None,
) -> dict[str, str | int | None]:
    """Fetch the raw git diff for a GitHub Pull Request.

    Authenticates with PyGithub, downloads the .diff payload for the given PR,
    and returns a truncated string safe to pass to an LLM, along with the actual
    commit SHA and merge timestamp.

    Args:
        repo_full_name: "owner/repo" format (e.g., "GoogleCloudPlatform/microservices-demo").
        pr_number: The PR number to hydrate.
        github_token: Optional override; falls back to GITHUB_TOKEN env var.

    Returns:
        Dictionary containing:
        - diff_text: Raw diff text string (possibly truncated to MAX_TOKENS).
        - commit_sha: The merge commit SHA of the PR.
        - merged_at_timestamp: Unix epoch timestamp of when the PR was merged.

    Raises:
        ValueError: If GITHUB_TOKEN is not set and no token is provided.
        RuntimeError: If the GitHub API call fails (rate limit, 404, etc.).
    """
    token = github_token or os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "GITHUB_TOKEN is not set. Set it in your .env file or pass it explicitly."
        )

    try:
        gh = Github(auth=Auth.Token(token))
        repo = gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        logger.info(
            "Hydrating PR #%d from %s (title: %r, files: %d)",
            pr_number, repo_full_name, pr.title, pr.changed_files,
        )

        # Get the raw diff via the GitHub compare API
        # PyGithub doesn't expose raw diff directly; we use the commits comparison
        diff_text = _fetch_raw_diff(repo, pr)

        truncated = truncate_diff(diff_text, max_tokens=MAX_TOKENS)

        logger.info(
            "PR #%d diff: %d chars raw → %d chars after truncation",
            pr_number, len(diff_text), len(truncated),
        )

        merged_at = pr.merged_at
        timestamp = int(merged_at.timestamp()) if merged_at else None

        return {
            "diff_text": truncated,
            "commit_sha": pr.merge_commit_sha,
            "merged_at_timestamp": timestamp,
        }

    except GithubException as e:
        raise RuntimeError(
            f"GitHub API error fetching PR #{pr_number} from {repo_full_name}: "
            f"{e.status} {e.data}"
        ) from e


def truncate_diff(diff_text: str, max_tokens: int = MAX_TOKENS) -> str:
    """Truncate a diff string to fit within the LLM token budget.

    Strategy:
      1. If the diff fits within budget → return as-is.
      2. If over budget → slice at the character limit.
      3. Always preserve "diff --git" file headers so the LLM knows which
         files were touched, even if the actual hunks are cut off.
      4. Append a clear truncation notice so the LLM knows it has partial data.

    Args:
        diff_text: Raw git diff string.
        max_tokens: Token budget (default: 50,000).

    Returns:
        Diff string truncated to the token budget.
    """
    max_chars = max_tokens * CHARS_PER_TOKEN

    if len(diff_text) <= max_chars:
        return diff_text

    # Slice at the character budget
    truncated = diff_text[:max_chars]

    # Find the last complete line to avoid cutting mid-line
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]

    # Extract file headers from the FULL diff (so we always have the file list)
    file_headers = _extract_file_headers(diff_text)
    header_summary = "\n".join(file_headers)

    notice = (
        f"\n\n[TESSON TRUNCATION NOTICE]\n"
        f"Diff truncated at {max_tokens:,} tokens ({max_chars:,} chars).\n"
        f"Original diff: {len(diff_text):,} chars "
        f"(~{len(diff_text) // CHARS_PER_TOKEN:,} tokens).\n"
        f"All changed files ({len(file_headers)}):\n{header_summary}\n"
    )

    return truncated + notice


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _fetch_raw_diff(repo, pr) -> str:
    """Download the raw unified diff for a PR using PyGithub's file API.

    PyGithub doesn't directly expose the raw .diff endpoint, so we reconstruct
    a simplified diff string from the PullRequestFile objects. Each file includes
    the patch (unified diff hunk) if available.
    """
    diff_parts: list[str] = []

    for pr_file in pr.get_files():
        header = f"diff --git a/{pr_file.filename} b/{pr_file.filename}\n"
        header += f"--- a/{pr_file.filename}\n"
        header += f"+++ b/{pr_file.filename}\n"
        header += f"# status: {pr_file.status} | +{pr_file.additions} -{pr_file.deletions}\n"

        patch = getattr(pr_file, "patch", None) or "[binary or large file — patch unavailable]"
        diff_parts.append(header + patch + "\n")

    return "\n".join(diff_parts) if diff_parts else "[No file changes found in this PR]"


def _extract_file_headers(diff_text: str) -> list[str]:
    """Extract all 'diff --git' lines from a diff for the truncation summary."""
    return [
        line for line in diff_text.splitlines()
        if line.startswith("diff --git")
    ]
