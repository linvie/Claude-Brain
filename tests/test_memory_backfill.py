"""brain/memory/backfill.py 单元测试 — 历史飞书 session 回填。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from brain.infra.db import _init_memory_sessions
from brain.memory import backfill
from brain.memory.store import init_memory_tables


def _make_conn():
    """创建带 memories + memory_sessions 表的内存 DB。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_memory_tables(conn)
    _init_memory_sessions(conn)
    return conn


def _write_sdk_jsonl(path: Path, turns: int = 5):
    """写入 SDK 格式的 JSONL 文件。"""
    with open(path, "w") as f:
        for i in range(turns):
            user_entry = {
                "type": "user",
                "message": {"role": "user", "content": f"用户消息 {i}"},
                "timestamp": f"2026-04-10T0{i}:00:00Z",
            }
            assistant_entry = {
                "type": "assistant",
                "message": {"role": "assistant", "content": f"助手回复 {i}"},
                "timestamp": f"2026-04-10T0{i}:01:00Z",
            }
            f.write(json.dumps(user_entry, ensure_ascii=False) + "\n")
            f.write(json.dumps(assistant_entry, ensure_ascii=False) + "\n")


class TestParseTimestamps:
    def test_extracts_first_and_last(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_sdk_jsonl(jsonl, turns=3)
        first, last = backfill._parse_timestamps(jsonl)
        assert first is not None
        assert last is not None
        assert last >= first

    def test_returns_none_for_empty(self, tmp_path):
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")
        first, last = backfill._parse_timestamps(jsonl)
        assert first is None
        assert last is None

    def test_handles_invalid_json(self, tmp_path):
        jsonl = tmp_path / "bad.jsonl"
        jsonl.write_text("not json\n{}\n")
        first, last = backfill._parse_timestamps(jsonl)
        assert first is None


class TestCountUserTurns:
    def test_counts_user_entries(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_sdk_jsonl(jsonl, turns=5)
        count = backfill._count_user_turns(jsonl)
        assert count == 5

    def test_zero_for_empty(self, tmp_path):
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")
        assert backfill._count_user_turns(jsonl) == 0

    def test_ignores_assistant_only(self, tmp_path):
        jsonl = tmp_path / "asst.jsonl"
        with open(jsonl, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hi"}}) + "\n")
        assert backfill._count_user_turns(jsonl) == 0


class TestConvertSdkJsonl:
    def test_converts_user_and_assistant(self, tmp_path):
        src = tmp_path / "src.jsonl"
        dest = tmp_path / "dest.jsonl"
        _write_sdk_jsonl(src, turns=2)

        backfill._convert_sdk_jsonl(src, dest)

        entries = []
        with open(dest) as f:
            for line in f:
                entries.append(json.loads(line))

        assert len(entries) == 4
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_skips_non_user_assistant(self, tmp_path):
        src = tmp_path / "src.jsonl"
        dest = tmp_path / "dest.jsonl"
        with open(src, "w") as f:
            f.write(json.dumps({"type": "system", "message": {"role": "system", "content": "x"}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}) + "\n")

        backfill._convert_sdk_jsonl(src, dest)

        entries = [json.loads(line) for line in open(dest)]
        assert len(entries) == 1
        assert entries[0]["role"] == "user"


class TestSessionStatus:
    def test_new_session(self):
        conn = _make_conn()
        assert backfill._session_status(conn, "nonexistent") == "new"

    def test_needs_extraction(self):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at) VALUES (?, ?, ?)",
            ("test-id", "ch1", 1000),
        )
        assert backfill._session_status(conn, "test-id") == "needs_extraction"

    def test_done(self):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at, extracted_at) VALUES (?, ?, ?, ?)",
            ("test-id", "ch1", 1000, 2000),
        )
        assert backfill._session_status(conn, "test-id") == "done"


class TestDiscoverSessions:
    def test_skips_active_session(self, tmp_path):
        jsonl = tmp_path / f"{backfill.ACTIVE_SESSION_ID}.jsonl"
        _write_sdk_jsonl(jsonl, turns=5)

        with patch.object(backfill, "SOURCE_DIR", tmp_path):
            sessions = backfill.discover_sessions()

        assert len(sessions) == 0

    def test_skips_short_sessions(self, tmp_path):
        jsonl = tmp_path / "short-session.jsonl"
        _write_sdk_jsonl(jsonl, turns=2)

        with patch.object(backfill, "SOURCE_DIR", tmp_path):
            sessions = backfill.discover_sessions()

        assert len(sessions) == 0

    def test_discovers_valid_session(self, tmp_path):
        jsonl = tmp_path / "valid-session.jsonl"
        _write_sdk_jsonl(jsonl, turns=5)

        with patch.object(backfill, "SOURCE_DIR", tmp_path):
            sessions = backfill.discover_sessions()

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "valid-session"
        assert sessions[0]["turns"] == 5

    def test_skips_empty_file(self, tmp_path):
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")

        with patch.object(backfill, "SOURCE_DIR", tmp_path):
            sessions = backfill.discover_sessions()

        assert len(sessions) == 0

    def test_missing_source_dir(self, tmp_path):
        with patch.object(backfill, "SOURCE_DIR", tmp_path / "nonexistent"):
            sessions = backfill.discover_sessions()
        assert sessions == []


class TestBackfillOne:
    @pytest.mark.asyncio
    async def test_skips_already_done(self, tmp_path):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at, extracted_at) VALUES (?, ?, ?, ?)",
            ("done-id", "ch1", 1000, 2000),
        )
        session = {"session_id": "done-id", "jsonl_path": tmp_path / "x.jsonl", "opened_at": 1000, "closed_at": 2000, "turns": 5}
        result = await backfill.backfill_one(conn, session)
        assert result == -1

    @pytest.mark.asyncio
    async def test_inserts_new_session(self, tmp_path):
        conn = _make_conn()
        src = tmp_path / "new-session.jsonl"
        _write_sdk_jsonl(src, turns=5)

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()

        session = {
            "session_id": "new-session",
            "jsonl_path": src,
            "opened_at": 1000,
            "closed_at": 2000,
            "turns": 5,
        }

        with patch.object(backfill, "MEMORY_LEDGER_DIR", ledger_dir), \
             patch("brain.memory.backfill.extract_from_session", new_callable=AsyncMock, return_value=3):
            result = await backfill.backfill_one(conn, session)

        assert result == 3
        row = conn.execute("SELECT * FROM memory_sessions WHERE session_id = ?", ("new-session",)).fetchone()
        assert row is not None
        assert row["channel_id"] == backfill.CHANNEL_ID
        assert row["opened_at"] == 1000
        assert row["closed_at"] == 2000

    @pytest.mark.asyncio
    async def test_converts_and_archives_jsonl(self, tmp_path):
        conn = _make_conn()
        src = tmp_path / "src-session.jsonl"
        _write_sdk_jsonl(src, turns=4)

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()

        session = {
            "session_id": "src-session",
            "jsonl_path": src,
            "opened_at": 1000,
            "closed_at": 2000,
            "turns": 4,
        }

        with patch.object(backfill, "MEMORY_LEDGER_DIR", ledger_dir), \
             patch("brain.memory.backfill.extract_from_session", new_callable=AsyncMock, return_value=0):
            await backfill.backfill_one(conn, session)

        ledger_file = ledger_dir / "src-session.jsonl"
        assert ledger_file.exists()
        entries = [json.loads(line) for line in open(ledger_file)]
        assert all("role" in e for e in entries)

    @pytest.mark.asyncio
    async def test_idempotent_ledger_copy(self, tmp_path):
        """If ledger file already exists, don't overwrite it."""
        conn = _make_conn()
        src = tmp_path / "idem-session.jsonl"
        _write_sdk_jsonl(src, turns=4)

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        existing = ledger_dir / "idem-session.jsonl"
        existing.write_text("existing content\n")

        session = {
            "session_id": "idem-session",
            "jsonl_path": src,
            "opened_at": 1000,
            "closed_at": 2000,
            "turns": 4,
        }

        with patch.object(backfill, "MEMORY_LEDGER_DIR", ledger_dir), \
             patch("brain.memory.backfill.extract_from_session", new_callable=AsyncMock, return_value=0):
            await backfill.backfill_one(conn, session)

        assert existing.read_text() == "existing content\n"
