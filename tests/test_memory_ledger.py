"""memory/ledger.py 单元测试 — JSONL 归档管理。"""

import pytest

from brain.memory import ledger


@pytest.fixture
def tmp_ledger(tmp_path, monkeypatch):
    """将 MEMORY_LEDGER_DIR 指向临时目录。"""
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setattr(ledger, "MEMORY_LEDGER_DIR", ledger_dir)
    return ledger_dir


class TestGetLedgerDir:
    def test_creates_dir_if_missing(self, tmp_ledger):
        assert not tmp_ledger.exists()
        result = ledger.get_ledger_dir()
        assert result == tmp_ledger
        assert tmp_ledger.is_dir()

    def test_returns_existing_dir(self, tmp_ledger):
        tmp_ledger.mkdir(parents=True)
        result = ledger.get_ledger_dir()
        assert result == tmp_ledger


class TestArchiveSessionJsonl:
    def test_copies_file(self, tmp_ledger, tmp_path):
        src = tmp_path / "source.jsonl"
        src.write_text('{"role":"user","content":"hello"}\n')

        result = ledger.archive_session_jsonl("sess-123", src)

        assert result is not None
        assert result == tmp_ledger / "sess-123.jsonl"
        assert result.read_text() == src.read_text()

    def test_source_missing_returns_none(self, tmp_ledger, tmp_path):
        missing = tmp_path / "nonexistent.jsonl"
        result = ledger.archive_session_jsonl("sess-456", missing)
        assert result is None

    def test_source_empty_returns_none(self, tmp_ledger, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        result = ledger.archive_session_jsonl("sess-789", empty)
        assert result is None


class TestGetSessionJsonl:
    def test_existing_file(self, tmp_ledger):
        tmp_ledger.mkdir(parents=True)
        f = tmp_ledger / "sess-abc.jsonl"
        f.write_text("data")

        result = ledger.get_session_jsonl("sess-abc")
        assert result == f

    def test_missing_file_returns_none(self, tmp_ledger):
        tmp_ledger.mkdir(parents=True)
        result = ledger.get_session_jsonl("nonexistent")
        assert result is None
