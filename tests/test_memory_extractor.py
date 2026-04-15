"""brain/memory/extractor.py 单元测试 — LLM 记忆提取。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

from brain.infra.db import _init_memory_sessions
from brain.memory import extractor
from brain.memory.store import init_memory_tables


def _make_conn():
    """创建带 memories + memory_sessions 表的内存 DB。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_memory_tables(conn)
    _init_memory_sessions(conn)
    return conn


def _write_jsonl(path: Path, entries: list[dict]):
    """写入 JSONL 文件。"""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _make_conversation(turns: int = 5) -> list[dict]:
    """生成模拟对话 JSONL entries。"""
    entries = []
    for i in range(turns):
        entries.append({"role": "user", "content": f"用户消息 {i}"})
        entries.append({"role": "assistant", "content": f"助手回复 {i}"})
    return entries


class TestParseJsonl:
    def test_normal_entries(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ])
        result = extractor._parse_jsonl(jsonl)
        assert len(result) == 2
        assert result[0] == ("user", "hello")
        assert result[1] == ("assistant", "hi there")

    def test_content_blocks(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [
            {"role": "user", "content": [{"type": "text", "text": "block msg"}]},
        ])
        result = extractor._parse_jsonl(jsonl)
        assert result == [("user", "block msg")]

    def test_skips_system_role(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [
            {"role": "system", "content": "system msg"},
            {"role": "user", "content": "hello"},
        ])
        result = extractor._parse_jsonl(jsonl)
        assert len(result) == 1

    def test_empty_file(self, tmp_path):
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")
        assert extractor._parse_jsonl(jsonl) == []

    def test_malformed_json(self, tmp_path):
        jsonl = tmp_path / "bad.jsonl"
        jsonl.write_text("not json\n")
        assert extractor._parse_jsonl(jsonl) == []


class TestBuildConversationSummary:
    def test_short_conversation(self):
        conv = [("user", "hello"), ("assistant", "hi")]
        result = extractor._build_conversation_summary(conv)
        assert "用户: hello" in result
        assert "助手: hi" in result

    def test_truncation(self):
        long_msg = "x" * 10000
        conv = [("user", long_msg), ("assistant", long_msg)]
        result = extractor._build_conversation_summary(conv)
        assert len(result) <= extractor._MAX_CONVERSATION_CHARS + 50  # gap marker
        assert "对话中间省略" in result


class TestParseAndStore:
    def test_valid_output(self):
        conn = _make_conn()
        raw = (
            "fact|8|用户偏好 Python 开发\n"
            "preference|9|代码风格偏好简洁\n"
            "decision|7|选择 SQLite 而非 PostgreSQL\n"
        )
        count = extractor._parse_and_store(conn, raw, "sess-1", "ch-1")
        assert count == 3

        rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
        assert len(rows) == 3
        assert rows[0]["type"] == "fact"
        assert rows[0]["importance"] == 8
        assert "Python" in rows[0]["content"]
        assert rows[0]["scope"] == "channel:ch-1"

    def test_skips_invalid_lines(self):
        conn = _make_conn()
        raw = (
            "fact|8|valid line\n"
            "invalid line\n"
            "\n"
            "bad|format\n"
            "context|5|also valid\n"
        )
        count = extractor._parse_and_store(conn, raw, "sess-2", "ch-2")
        assert count == 2

    def test_clamps_importance(self):
        conn = _make_conn()
        raw = "fact|15|importance over 10\nfact|0|importance under 1\n"
        count = extractor._parse_and_store(conn, raw, "sess-3", "ch-3")
        assert count == 2

        rows = conn.execute("SELECT importance FROM memories ORDER BY id").fetchall()
        assert rows[0]["importance"] == 10
        assert rows[1]["importance"] == 1

    def test_skips_short_content(self):
        conn = _make_conn()
        raw = "fact|5|abc\n"  # < 5 chars
        count = extractor._parse_and_store(conn, raw, "sess-4", "ch-4")
        assert count == 0

    def test_global_scope_when_no_channel(self):
        conn = _make_conn()
        raw = "fact|5|some fact here\n"
        extractor._parse_and_store(conn, raw, "sess-5", "")
        row = conn.execute("SELECT scope FROM memories").fetchone()
        assert row["scope"] == "global"


class TestExtractFromSession:
    async def test_missing_jsonl(self):
        conn = _make_conn()
        count = await extractor.extract_from_session(
            conn, "sess-1", Path("/nonexistent.jsonl"), "ch-1"
        )
        assert count == 0

    async def test_too_few_turns(self, tmp_path):
        conn = _make_conn()
        jsonl = tmp_path / "short.jsonl"
        _write_jsonl(jsonl, _make_conversation(turns=2))  # < _MIN_TURNS

        count = await extractor.extract_from_session(conn, "sess-2", jsonl, "ch-2")
        assert count == 0

    async def test_haiku_extraction(self, tmp_path):
        conn = _make_conn()
        # 先插入一个 memory_session 记录
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at) "
            "VALUES (?, ?, ?)",
            ("sess-3", "ch-3", 1000),
        )
        conn.commit()

        jsonl = tmp_path / "conv.jsonl"
        _write_jsonl(jsonl, _make_conversation(turns=5))

        haiku_output = (
            "fact|7|用户正在开发一个 AI daemon 系统\n"
            "preference|8|偏好使用 SQLite 作为本地存储\n"
        )

        with patch.object(extractor, "haiku_complete", new=AsyncMock(return_value=haiku_output)):
            count = await extractor.extract_from_session(conn, "sess-3", jsonl, "ch-3")

        assert count == 2
        rows = conn.execute("SELECT * FROM memories").fetchall()
        assert len(rows) == 2
        assert any("AI daemon" in r["content"] for r in rows)

        # 验证 extracted_at 被更新
        sess = conn.execute(
            "SELECT extracted_at FROM memory_sessions WHERE session_id = ?",
            ("sess-3",),
        ).fetchone()
        assert sess["extracted_at"] is not None

    async def test_haiku_empty_output(self, tmp_path):
        conn = _make_conn()
        jsonl = tmp_path / "conv.jsonl"
        _write_jsonl(jsonl, _make_conversation(turns=5))

        with patch.object(extractor, "haiku_complete", new=AsyncMock(return_value="")):
            count = await extractor.extract_from_session(conn, "sess-4", jsonl, "ch-4")

        assert count == 0


class TestExtractAndStoreLegacy:
    """Phase A 兼容接口测试。"""

    def test_extracts_matching_pattern(self):
        conn = _make_conn()
        text = "在对话中用户提到他最近在学习 Rust 编程语言，有很大的兴趣。"
        count = extractor.extract_and_store(conn, text, "test-source")
        assert count >= 1

    def test_empty_input(self):
        conn = _make_conn()
        assert extractor.extract_and_store(conn, "", "src") == 0
        assert extractor.extract_and_store(conn, "short", "src") == 0
