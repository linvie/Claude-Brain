"""Tests for _parse_jsonl compatibility with old and new SDK JSONL schemas."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from brain.memory.extractor import _parse_jsonl

# ── Fixtures ──


def _write_jsonl(lines: list[dict]) -> Path:
    """Write a list of dicts as a JSONL temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for entry in lines:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    f.close()
    return Path(f.name)


# Old schema: top-level {role, content}
OLD_SCHEMA_LINES = [
    {"role": "user", "content": "Hi"},
    {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": "Let me think about this...",
                "signature": "sig123",
            },
            {"type": "text", "text": "你好！有什么可以帮你的吗？"},
        ],
    },
    {"role": "user", "content": "查一下系统状态"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "系统运行正常。"},
        ],
    },
]

# New SDK schema: {type, message: {role, content}, ...}
NEW_SDK_SCHEMA_LINES = [
    # queue-operation entries (should be skipped)
    {
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": "2026-04-17T05:48:25.332Z",
        "sessionId": "edd075c5",
        "content": "检查记忆系统",
    },
    {
        "type": "queue-operation",
        "operation": "dequeue",
        "timestamp": "2026-04-17T05:48:25.333Z",
        "sessionId": "edd075c5",
    },
    # user message (new schema)
    {
        "type": "user",
        "message": {"role": "user", "content": "检查一下记忆系统是否正常运行"},
        "uuid": "36c7026d",
        "timestamp": "2026-04-17T05:48:25.336Z",
    },
    # attachment entry (should be skipped)
    {
        "type": "attachment",
        "parentUuid": "36c7026d",
        "attachment": {"type": "deferred_tools_delta"},
    },
    # assistant with thinking + text blocks (new schema)
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "用户想检查记忆系统...",
                    "signature": "EtwCClk",
                },
                {"type": "text", "text": "记忆系统运行正常。"},
            ],
        },
        "uuid": "abc123",
        "timestamp": "2026-04-17T05:48:30.000Z",
    },
    # assistant with tool_use (should produce no text)
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "Read",
                    "input": {"file_path": "/tmp/test"},
                },
            ],
        },
        "uuid": "def456",
    },
    # user follow-up
    {
        "type": "user",
        "message": {"role": "user", "content": "谢谢"},
        "uuid": "ghi789",
    },
    # assistant final reply
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "不客气！"}],
        },
        "uuid": "jkl012",
    },
]


# ── Tests ──


class TestParseJsonlOldSchema:
    """Old schema: top-level {role, content}."""

    def test_returns_correct_roles_and_text(self):
        path = _write_jsonl(OLD_SCHEMA_LINES)
        result = _parse_jsonl(path)

        assert len(result) == 4
        assert result[0] == ("user", "Hi")
        assert result[1] == ("assistant", "你好！有什么可以帮你的吗？")
        assert result[2] == ("user", "查一下系统状态")
        assert result[3] == ("assistant", "系统运行正常。")

    def test_thinking_blocks_are_excluded(self):
        """Thinking blocks in old schema should not appear in text."""
        path = _write_jsonl(OLD_SCHEMA_LINES)
        result = _parse_jsonl(path)

        # The assistant entry with thinking block should only contain the text part
        _, text = result[1]
        assert "think" not in text.lower()
        assert "你好" in text


class TestParseJsonlNewSdkSchema:
    """New SDK schema: {type, message: {role, content}, ...}."""

    def test_returns_correct_roles_and_text(self):
        path = _write_jsonl(NEW_SDK_SCHEMA_LINES)
        result = _parse_jsonl(path)

        # Should extract: user("检查..."), assistant("记忆...正常"), user("谢谢"), assistant("不客气")
        # Tool-use-only assistant entry should be skipped (empty text)
        assert len(result) == 4
        assert result[0] == ("user", "检查一下记忆系统是否正常运行")
        assert result[1] == ("assistant", "记忆系统运行正常。")
        assert result[2] == ("user", "谢谢")
        assert result[3] == ("assistant", "不客气！")

    def test_queue_operation_entries_skipped(self):
        """queue-operation entries should not appear in conversation."""
        path = _write_jsonl(NEW_SDK_SCHEMA_LINES)
        result = _parse_jsonl(path)

        texts = [text for _, text in result]
        assert "检查记忆系统" not in texts  # queue-operation content

    def test_thinking_blocks_excluded(self):
        path = _write_jsonl(NEW_SDK_SCHEMA_LINES)
        result = _parse_jsonl(path)

        all_text = " ".join(text for _, text in result)
        assert "用户想检查记忆系统" not in all_text

    def test_tool_use_only_entry_produces_no_text(self):
        """An assistant entry with only tool_use blocks should be skipped."""
        path = _write_jsonl(NEW_SDK_SCHEMA_LINES)
        result = _parse_jsonl(path)

        texts = [text for _, text in result]
        assert "Read" not in " ".join(texts)


class TestParseJsonlEdgeCases:
    def test_empty_file(self):
        path = _write_jsonl([])
        assert _parse_jsonl(path) == []

    def test_nonexistent_file(self):
        assert _parse_jsonl(Path("/nonexistent/file.jsonl")) == []

    def test_mixed_schemas(self):
        """A file with both old and new schema entries."""
        mixed = [
            {"role": "user", "content": "old style"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "new style"}],
                },
            },
        ]
        path = _write_jsonl(mixed)
        result = _parse_jsonl(path)
        assert len(result) == 2
        assert result[0] == ("user", "old style")
        assert result[1] == ("assistant", "new style")

    def test_string_content_in_new_schema(self):
        """New schema with string content (not list)."""
        lines = [
            {
                "type": "user",
                "message": {"role": "user", "content": "plain string"},
            },
        ]
        path = _write_jsonl(lines)
        result = _parse_jsonl(path)
        assert result == [("user", "plain string")]
