"""
Stage 4 Verify — Unit tests for the diff hydrator.

Uses unittest.mock to avoid real GitHub API calls.
Tests cover: truncation logic, file header extraction, error handling.
"""

from unittest.mock import MagicMock, patch

import pytest

from ingestion.hydrator import (
    MAX_CHARS,
    MAX_TOKENS,
    _extract_file_headers,
    hydrate_pr_diff,
    truncate_diff,
)

# ─── Truncation Tests (pure functions — no mocking needed) ───────────────────

class TestTruncateDiff:
    def test_short_diff_unchanged(self):
        diff = "diff --git a/foo.py b/foo.py\n+some change\n"
        result = truncate_diff(diff, max_tokens=10000)
        assert result == diff  # fits, no truncation

    def test_long_diff_is_truncated(self):
        # Create a diff larger than 50k tokens (200k chars)
        big_diff = "diff --git a/huge.py b/huge.py\n" + ("x" * (MAX_CHARS + 10000))
        result = truncate_diff(big_diff, max_tokens=MAX_TOKENS)
        assert len(result) < len(big_diff)
        assert "TESSON TRUNCATION NOTICE" in result

    def test_truncated_diff_preserves_file_headers(self):
        header1 = "diff --git a/service_a.py b/service_a.py"
        header2 = "diff --git a/service_b.py b/service_b.py"
        padding = "x" * (MAX_CHARS + 5000)
        big_diff = f"{header1}\n{padding}\n{header2}\n"
        result = truncate_diff(big_diff, max_tokens=MAX_TOKENS)
        # Both file headers must appear in the truncation notice
        assert "service_a.py" in result
        assert "service_b.py" in result

    def test_truncation_ends_on_line_boundary(self):
        # Build a diff that goes just over the limit mid-line
        line = "+" + "a" * 100 + "\n"
        lines = [line] * (MAX_CHARS // len(line) + 5)
        big_diff = "diff --git a/foo.py b/foo.py\n" + "".join(lines)
        result = truncate_diff(big_diff, max_tokens=MAX_TOKENS)
        # Should not end mid-line
        non_notice_part = result.split("[TESSON TRUNCATION NOTICE]")[0]
        assert non_notice_part.endswith("\n") or non_notice_part == ""

    def test_custom_token_limit(self):
        diff = "a" * 1000  # 1000 chars = 250 tokens
        result = truncate_diff(diff, max_tokens=100)  # limit to 400 chars
        assert len(result) <= 400 + len("\n\n[TESSON TRUNCATION NOTICE]") + 500


class TestExtractFileHeaders:
    def test_extracts_diff_headers(self):
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/bar.py b/bar.py\n"
            "--- a/bar.py\n"
        )
        headers = _extract_file_headers(diff)
        assert len(headers) == 2
        assert "diff --git a/foo.py b/foo.py" in headers
        assert "diff --git a/bar.py b/bar.py" in headers

    def test_empty_diff_returns_empty_list(self):
        assert _extract_file_headers("") == []

    def test_no_headers_returns_empty_list(self):
        diff = "+some addition\n-some deletion\n"
        assert _extract_file_headers(diff) == []


# ─── hydrate_pr_diff (mocked PyGithub) ───────────────────────────────────────

class TestHydratePrDiff:
    def _make_mock_file(self, filename, status="modified", additions=5, deletions=2, patch=None):
        f = MagicMock()
        f.filename = filename
        f.status = status
        f.additions = additions
        f.deletions = deletions
        f.patch = patch or f"+new line in {filename}\n-old line in {filename}"
        return f

    @patch("ingestion.hydrator.Github")
    def test_returns_diff_string(self, mock_github_cls):
        mock_file = self._make_mock_file("src/cartservice/main.go")
        mock_pr = MagicMock()
        mock_pr.title = "Fix cart Redis connection"
        mock_pr.changed_files = 1
        mock_pr.get_files.return_value = [mock_file]

        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr

        mock_gh = MagicMock()
        mock_gh.get_repo.return_value = mock_repo
        mock_github_cls.return_value = mock_gh

        result = hydrate_pr_diff(
            "GoogleCloudPlatform/microservices-demo",
            pr_number=42,
            github_token="fake-token",
        )
        assert "src/cartservice/main.go" in result["diff_text"]
        assert isinstance(result, dict)
        assert len(result["diff_text"]) > 0

    @patch("ingestion.hydrator.Github")
    def test_multiple_files_included(self, mock_github_cls):
        files = [
            self._make_mock_file("service_a/main.py"),
            self._make_mock_file("service_b/config.yaml"),
            self._make_mock_file("service_c/Dockerfile"),
        ]
        mock_pr = MagicMock()
        mock_pr.title = "Multi-file change"
        mock_pr.changed_files = 3
        mock_pr.get_files.return_value = files

        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_gh = MagicMock()
        mock_gh.get_repo.return_value = mock_repo
        mock_github_cls.return_value = mock_gh

        result = hydrate_pr_diff("owner/repo", 1, github_token="fake-token")
        assert "service_a/main.py" in result["diff_text"]
        assert "service_b/config.yaml" in result["diff_text"]
        assert "service_c/Dockerfile" in result["diff_text"]

    def test_missing_token_raises_value_error(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            hydrate_pr_diff("owner/repo", 1, github_token=None)

    @patch("ingestion.hydrator.Github")
    def test_github_exception_raises_runtime_error(self, mock_github_cls):
        from github import GithubException
        mock_gh = MagicMock()
        mock_gh.get_repo.side_effect = GithubException(404, {"message": "Not Found"})
        mock_github_cls.return_value = mock_gh

        with pytest.raises(RuntimeError, match="GitHub API error"):
            hydrate_pr_diff("owner/nonexistent", 1, github_token="fake-token")
