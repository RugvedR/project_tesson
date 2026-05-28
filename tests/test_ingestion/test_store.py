"""
Stage 6 Verify — Unit tests for the JSONL ledger store.

Tests cover: append/read round-trip, multiple records, streaming,
corrupted line skipping, and count().
"""



from ingestion.schemas import TessonSimplex
from ledger import store


def make_simplex(nodes=None, pr_number=None) -> TessonSimplex:
    return TessonSimplex(
        nodes=nodes or ["service-a", "redis-cache"],
        timestamp=1700000000,
        pr_number=pr_number,
        metadata={"test": "true"},
    )


# ─── Append + Read Round-Trip ─────────────────────────────────────────────────

class TestAppendAndRead:
    def test_append_single_record(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        s = make_simplex()
        store.append(s, ledger_path=path)
        assert path.exists()
        records = store.read_all(ledger_path=path)
        assert len(records) == 1
        assert records[0].simplex_id == s.simplex_id

    def test_append_multiple_records(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        simplices = [make_simplex(pr_number=i) for i in range(1, 6)]
        for s in simplices:
            store.append(s, ledger_path=path)
        records = store.read_all(ledger_path=path)
        assert len(records) == 5

    def test_order_preserved(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        for i in range(1, 4):
            store.append(make_simplex(pr_number=i), ledger_path=path)
        records = store.read_all(ledger_path=path)
        pr_numbers = [r.pr_number for r in records]
        assert pr_numbers == [1, 2, 3]

    def test_nodes_round_trip_correctly(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        s = make_simplex(nodes=["cart-service", "postgres-db", "redis-cache"])
        store.append(s, ledger_path=path)
        records = store.read_all(ledger_path=path)
        assert records[0].nodes == ["cart-service", "postgres-db", "redis-cache"]

    def test_metadata_round_trip(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        s = TessonSimplex(
            nodes=["a1", "b1"],
            metadata={"repo": "test-repo", "env": "staging"},
        )
        store.append(s, ledger_path=path)
        records = store.read_all(ledger_path=path)
        assert records[0].metadata["repo"] == "test-repo"


# ─── Streaming ────────────────────────────────────────────────────────────────

class TestStream:
    def test_stream_yields_correct_count(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        for i in range(10):
            store.append(make_simplex(pr_number=i + 1), ledger_path=path)
        yielded = list(store.stream(ledger_path=path))
        assert len(yielded) == 10

    def test_stream_empty_ledger_yields_nothing(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        yielded = list(store.stream(ledger_path=path))
        assert yielded == []

    def test_stream_nonexistent_ledger_yields_nothing(self, tmp_path):
        path = tmp_path / "does_not_exist.jsonl"
        yielded = list(store.stream(ledger_path=path))
        assert yielded == []


# ─── Corrupted Lines ──────────────────────────────────────────────────────────

class TestCorruptedLines:
    def test_corrupted_line_is_skipped(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        # Write one valid + one corrupted line manually
        s = make_simplex()
        store.append(s, ledger_path=path)
        with path.open("a") as f:
            f.write('{"invalid": "not a simplex"}\n')
        # Should get 1 valid record and skip the corrupted one
        records = store.read_all(ledger_path=path)
        assert len(records) == 1
        assert records[0].simplex_id == s.simplex_id

    def test_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        store.append(make_simplex(pr_number=1), ledger_path=path)
        with path.open("a") as f:
            f.write("\n\n\n")
        store.append(make_simplex(pr_number=2), ledger_path=path)
        records = store.read_all(ledger_path=path)
        assert len(records) == 2


# ─── Count ────────────────────────────────────────────────────────────────────

class TestCount:
    def test_count_empty(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        assert store.count(ledger_path=path) == 0

    def test_count_after_appends(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        for _ in range(7):
            store.append(make_simplex(), ledger_path=path)
        assert store.count(ledger_path=path) == 7


# ─── Directory Creation ───────────────────────────────────────────────────────

class TestDirectoryCreation:
    def test_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "ledger.jsonl"
        store.append(make_simplex(), ledger_path=deep_path)
        assert deep_path.exists()
