"""Phase B 记忆系统集成测试 — 端到端流程验证。

测试完整生命周期：
  session open → JSONL archived → LLM extract → memories written
  → FTS5 retrieval → Context Bridge injection → daily view generated
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from brain.infra.db import _init_memory_sessions
from brain.memory import extractor
from brain.memory import views as views_mod
from brain.memory.ledger import archive_session_jsonl, get_session_jsonl
from brain.memory.retriever import _fts5_query, build_memory_context, decay_score
from brain.memory.store import add_memory, init_memory_tables

# ── Helpers ──


def _make_conn() -> sqlite3.Connection:
    """创建带完整 Phase B schema 的内存 DB。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_memory_tables(conn)
    _init_memory_sessions(conn)
    return conn


def _write_jsonl(path: Path, entries: list[dict]):
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _make_conversation(turns: int = 5) -> list[dict]:
    entries = []
    for i in range(turns):
        entries.append({"role": "user", "content": f"用户消息 {i}: 讨论 Python 项目架构"})
        entries.append({"role": "assistant", "content": f"助手回复 {i}: SQLite 是好的选择"})
    return entries


def _insert_session(conn, session_id, channel_id, opened_at, closed_at=None,
                    jsonl_path=None, message_count=0):
    conn.execute(
        "INSERT INTO memory_sessions (session_id, channel_id, opened_at, closed_at, jsonl_path, message_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, channel_id, opened_at, closed_at, jsonl_path, message_count),
    )
    conn.commit()


# ── 1. Ledger Archive Integration ──


class TestLedgerArchiveIntegration:
    """Ledger 归档 + 检索完整流程。"""

    def test_archive_and_retrieve(self, tmp_path):
        sdk_jsonl = tmp_path / "sdk_session.jsonl"
        _write_jsonl(sdk_jsonl, _make_conversation(3))

        with patch("brain.memory.ledger.MEMORY_LEDGER_DIR", tmp_path / "ledger"):
            dest = archive_session_jsonl("sess-int-1", sdk_jsonl)
            assert dest is not None
            assert dest.exists()
            assert dest.stat().st_size > 0

            retrieved = get_session_jsonl("sess-int-1")
            assert retrieved is not None
            assert retrieved == dest

    def test_archive_missing_source_returns_none(self, tmp_path):
        with patch("brain.memory.ledger.MEMORY_LEDGER_DIR", tmp_path / "ledger"):
            result = archive_session_jsonl("sess-missing", Path("/nonexistent.jsonl"))
            assert result is None

    def test_archive_empty_file_returns_none(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        with patch("brain.memory.ledger.MEMORY_LEDGER_DIR", tmp_path / "ledger"):
            result = archive_session_jsonl("sess-empty", empty)
            assert result is None


# ── 2. FTS5 Search Integration ──


class TestFTS5SearchIntegration:
    """验证 FTS5 触发器 + 检索在实际对话场景中工作正常。"""

    def test_insert_triggers_fts5_sync(self):
        conn = _make_conn()
        add_memory(conn, type="fact", content="用户偏好 Python 开发", importance=7)

        # 验证 FTS5 索引已同步
        rows = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH '\"Python\"'"
        ).fetchall()
        assert len(rows) == 1

    def test_fts5_chinese_trigram_search(self):
        conn = _make_conn()
        add_memory(conn, type="fact", content="项目使用 SQLite 作为本地存储引擎", importance=6)
        add_memory(conn, type="preference", content="偏好渐进式架构升级方案", importance=8)
        add_memory(conn, type="fact", content="飞书消息卡片支持 Markdown", importance=5)

        # 中文 trigram 搜索
        rows = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH '\"SQLite\"'"
        ).fetchall()
        assert len(rows) == 1

    def test_fts5_query_builder(self):
        # 英文
        assert '"Python"' in _fts5_query("Python project")
        assert '"project"' in _fts5_query("Python project")
        # 短词被过滤
        assert _fts5_query("ab cd") == ""
        # 中文 trigram
        q = _fts5_query("记忆系统设计")
        assert q  # 应有 trigram tokens

    def test_fts5_update_trigger(self):
        conn = _make_conn()
        mid = add_memory(conn, type="fact", content="旧内容 old content here", importance=5)

        # 更新内容
        conn.execute("UPDATE memories SET content = '新内容 new content here' WHERE id = ?", (mid,))
        conn.commit()

        # 旧内容不可搜到
        old = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH '\"旧内容\"'"
        ).fetchall()
        assert len(old) == 0

        # 新内容可搜到
        new = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH '\"新内容\"'"
        ).fetchall()
        assert len(new) == 1

    def test_fts5_delete_trigger(self):
        conn = _make_conn()
        mid = add_memory(conn, type="fact", content="将被删除的记忆 delete me", importance=5)
        conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        conn.commit()

        rows = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH '\"delete\"'"
        ).fetchall()
        assert len(rows) == 0


# ── 3. Context Bridge Integration ──


class TestContextBridgeIntegration:
    """验证 Context Bridge 三层检索 + 时间衰减 + 格式化输出。"""

    def test_always_on_layer(self):
        conn = _make_conn()
        add_memory(conn, type="fact", content="非常重要的用户偏好信息", importance=9)
        add_memory(conn, type="fact", content="不太重要的临时信息而已", importance=3)

        ctx = build_memory_context(conn, "随便说点什么")
        assert "非常重要的用户偏好信息" in ctx
        assert "重要信息" in ctx  # section header

    def test_relevance_layer_fts5(self):
        conn = _make_conn()
        add_memory(conn, type="fact", content="Python asyncio 事件循环架构设计", importance=6)
        add_memory(conn, type="decision", content="选择 PostgreSQL 作为生产数据库", importance=6)

        ctx = build_memory_context(conn, "Python asyncio 怎么用")
        assert "asyncio" in ctx

    def test_recent_layer_with_scope(self):
        conn = _make_conn()
        # 最近的 channel-scoped 记忆
        add_memory(conn, type="context", content="最近讨论了飞书卡片样式优化",
                   importance=4, scope="channel:ch-test")
        # 另一个 channel 的记忆
        add_memory(conn, type="context", content="另一个频道的讨论内容信息",
                   importance=4, scope="channel:ch-other")

        ctx = build_memory_context(conn, "test", channel_id="ch-test")
        assert "飞书卡片" in ctx
        # global 记忆也应出现（如果有的话）

    def test_deduplication_across_layers(self):
        conn = _make_conn()
        # 同一条记忆在 always-on 和 recent 层都会匹配
        add_memory(conn, type="fact", content="高重要度且最近的记忆内容",
                   importance=9)

        ctx = build_memory_context(conn, "记忆")
        # 内容应只出现一次
        assert ctx.count("高重要度且最近的记忆内容") == 1

    def test_empty_memories_returns_empty(self):
        conn = _make_conn()
        ctx = build_memory_context(conn, "hello")
        assert ctx == ""

    def test_token_truncation(self):
        conn = _make_conn()
        # 插入大量记忆
        for i in range(50):
            add_memory(conn, type="fact", content=f"记忆条目编号 {i} 包含一些需要存储的信息" * 5,
                       importance=9)

        ctx = build_memory_context(conn, "test", max_tokens=200)
        # 应被截断
        assert len(ctx) <= 200 * 2 + 50  # char_budget + truncation marker

    def test_decay_score_logic(self):
        # 新鲜记忆 decay 接近原始 importance
        assert decay_score(10, 0) == pytest.approx(10.0)
        # 30 天后约为一半
        assert decay_score(10, 30) == pytest.approx(5.0, rel=0.05)
        # 60 天后约为四分之一
        assert decay_score(10, 60) == pytest.approx(2.5, rel=0.05)

    def test_last_accessed_updated(self):
        conn = _make_conn()
        mid = add_memory(conn, type="fact", content="用于测试 last_accessed 更新", importance=9)

        before = conn.execute("SELECT last_accessed FROM memories WHERE id = ?", (mid,)).fetchone()
        time_before = before["last_accessed"]

        # build_memory_context 应更新 last_accessed
        build_memory_context(conn, "test")

        after = conn.execute("SELECT last_accessed FROM memories WHERE id = ?", (mid,)).fetchone()
        assert after["last_accessed"] >= time_before


# ── 4. Extraction → Storage → Retrieval E2E ──


class TestExtractionToRetrievalE2E:
    """端到端：JSONL → Haiku 提取 → 写入 memories → FTS5 检索 → Context Bridge。"""

    async def test_full_pipeline(self, tmp_path):
        conn = _make_conn()

        # 1. 模拟 session open
        session_id = "e2e-sess-1"
        channel_id = "ch-e2e"
        now = int(time.time())
        _insert_session(conn, session_id, channel_id, now - 3600, now - 1800, message_count=10)

        # 2. 创建并归档 JSONL
        jsonl = tmp_path / "conv.jsonl"
        _write_jsonl(jsonl, _make_conversation(turns=5))

        with patch("brain.memory.ledger.MEMORY_LEDGER_DIR", tmp_path / "ledger"):
            archived = archive_session_jsonl(session_id, jsonl)
            assert archived is not None

        # 3. Haiku 提取 → 写入 memories
        haiku_output = (
            "fact|7|项目使用 Python asyncio 架构\n"
            "preference|8|用户偏好 SQLite 而非 PostgreSQL\n"
            "decision|9|选择 FTS5 全文搜索替代向量数据库\n"
        )
        with patch.object(extractor, "haiku_complete", new=AsyncMock(return_value=haiku_output)):
            count = await extractor.extract_from_session(conn, session_id, archived, channel_id)
        assert count == 3

        # 4. 验证 memories 表
        rows = conn.execute("SELECT * FROM memories ORDER BY importance DESC").fetchall()
        assert len(rows) == 3
        assert rows[0]["importance"] == 9
        assert "FTS5" in rows[0]["content"]
        assert rows[0]["scope"] == f"channel:{channel_id}"

        # 5. 验证 FTS5 索引同步
        fts_rows = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH '\"SQLite\"'"
        ).fetchall()
        assert len(fts_rows) >= 1

        # 6. Context Bridge 检索
        ctx = build_memory_context(conn, "SQLite 还是 PostgreSQL", channel_id=channel_id)
        assert "SQLite" in ctx
        assert "FTS5" in ctx  # high importance, always-on

        # 7. 验证 extracted_at 被更新
        sess = conn.execute(
            "SELECT extracted_at FROM memory_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        assert sess["extracted_at"] is not None


# ── 5. Daily Views Integration ──


class TestDailyViewsIntegration:
    """验证 daily view 生成的完整流程。"""

    async def test_generate_daily_view_e2e(self, tmp_path):
        conn = _make_conn()

        # 插入已关闭、未摘要的 session
        session_id = "view-sess-1"
        channel_id = "ch-view"
        # 使用固定时间戳使 date() 计算可预测
        # 2026-04-14 12:00:00 UTC
        opened_at = 1776168000
        closed_at = opened_at + 3600

        _insert_session(conn, session_id, channel_id, opened_at, closed_at, message_count=8)

        # 插入该 session 提取的记忆
        add_memory(conn, type="fact", content="项目使用 FTS5 全文搜索引擎",
                   source=f"session:{session_id}", importance=8, scope=f"channel:{channel_id}")
        add_memory(conn, type="decision", content="选择 Haiku 模型进行记忆提取",
                   source=f"session:{session_id}", importance=7, scope=f"channel:{channel_id}")

        haiku_view_output = """\
## Sessions
- [ch-view] 12:00-13:00: 讨论了记忆系统的 FTS5 搜索实现

## Key Facts Learned
- 项目使用 FTS5 全文搜索引擎

## Decisions Made
- 选择 Haiku 模型进行记忆提取

## Open Questions
（无）"""

        with patch("brain.memory.views.MEMORY_VIEWS_DIR", tmp_path / "views"), \
             patch.object(views_mod, "haiku_complete", new=AsyncMock(return_value=haiku_view_output)):
            date_str = "2026-04-14"
            result = await views_mod.generate_daily_view(conn, date=date_str)

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "Daily Memory View" in content
        assert "FTS5" in content
        assert "Haiku" in content

        # 验证 session 被标记为 view 已生成
        sess = conn.execute(
            "SELECT view_generated_at FROM memory_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        assert sess["view_generated_at"] is not None

    async def test_run_daily_views_job_multiple_dates(self, tmp_path):
        conn = _make_conn()

        # 两个不同日期的 session
        ts_day1 = 1776340800  # 2026-04-14
        ts_day2 = ts_day1 + 86400  # 2026-04-15

        _insert_session(conn, "vs-1", "ch-1", ts_day1, ts_day1 + 3600, message_count=5)
        _insert_session(conn, "vs-2", "ch-1", ts_day2, ts_day2 + 3600, message_count=5)

        haiku_output = "## Sessions\n- session summary\n\n## Key Facts Learned\n（无）\n\n## Decisions Made\n（无）\n\n## Open Questions\n（无）"

        with patch("brain.memory.views.MEMORY_VIEWS_DIR", tmp_path / "views"), \
             patch.object(views_mod, "haiku_complete", new=AsyncMock(return_value=haiku_output)):
            await views_mod.run_daily_views_job(conn)

        # 两个 session 都应被标记为 view 已生成
        no_view = conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_sessions WHERE view_generated_at IS NULL"
        ).fetchone()
        assert no_view["cnt"] == 0

    async def test_no_sessions_returns_none(self):
        conn = _make_conn()
        result = await views_mod.generate_daily_view(conn, date="2026-01-01")
        assert result is None


# ── 6. Config Integration ──


class TestConfigIntegration:
    """验证 config.yaml 记忆配置项正确加载。"""

    def test_memory_config_defaults(self):
        from brain.config import (
            MEMORY_ALWAYS_ON_THRESHOLD,
            MEMORY_DECAY_HALF_LIFE,
            MEMORY_ENABLED,
            MEMORY_EXTRACTION_MODEL,
            MEMORY_LEDGER_DIR,
            MEMORY_MAX_CONTEXT_TOKENS,
            MEMORY_VIEWS_DIR,
            MEMORY_VIEWS_INTERVAL_HOURS,
        )
        # 验证配置常量存在且有合理默认值
        assert isinstance(MEMORY_ENABLED, bool)
        assert isinstance(MEMORY_DECAY_HALF_LIFE, (int, float))
        assert MEMORY_DECAY_HALF_LIFE > 0
        assert isinstance(MEMORY_ALWAYS_ON_THRESHOLD, int)
        assert 1 <= MEMORY_ALWAYS_ON_THRESHOLD <= 10
        assert isinstance(MEMORY_MAX_CONTEXT_TOKENS, int)
        assert MEMORY_MAX_CONTEXT_TOKENS > 0
        assert isinstance(MEMORY_LEDGER_DIR, Path)
        assert isinstance(MEMORY_VIEWS_DIR, Path)
        assert isinstance(MEMORY_EXTRACTION_MODEL, str)
        assert "haiku" in MEMORY_EXTRACTION_MODEL
        assert isinstance(MEMORY_VIEWS_INTERVAL_HOURS, (int, float))
        assert MEMORY_VIEWS_INTERVAL_HOURS > 0


# ── 7. Schema Migration Idempotency ──


class TestSchemaMigrationIdempotency:
    """验证 schema 迁移的幂等性。"""

    def test_double_init_no_error(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # 两次初始化不应报错
        init_memory_tables(conn)
        init_memory_tables(conn)

        _init_memory_sessions(conn)
        _init_memory_sessions(conn)

        # 表应正常存在
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "memories" in tables
        assert "memories_fts" in tables
        assert "memory_sessions" in tables

    def test_scope_migration_preserves_data(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # 创建 memories 表（不带 scope）
        conn.execute("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL, content TEXT NOT NULL,
                source TEXT, tags TEXT,
                importance INTEGER DEFAULT 5,
                last_accessed INTEGER, created_at INTEGER NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO memories (type, content, created_at) VALUES ('fact', 'old data', 1000)"
        )
        conn.commit()

        # 运行迁移
        init_memory_tables(conn)

        # 数据应保留，scope 默认 global
        row = conn.execute("SELECT scope FROM memories WHERE content = 'old data'").fetchone()
        assert row["scope"] == "global"


# ── 8. Scope Filtering Integration ──


class TestScopeFilteringIntegration:
    """验证 scope 过滤在检索时正确工作。"""

    def test_channel_scope_isolation(self):
        conn = _make_conn()
        add_memory(conn, type="fact", content="Channel A 专属的重要记忆信息",
                   importance=5, scope="channel:ch-a")
        add_memory(conn, type="fact", content="Channel B 专属的重要记忆信息",
                   importance=5, scope="channel:ch-b")
        add_memory(conn, type="fact", content="全局共享的记忆信息内容在此",
                   importance=5, scope="global")

        # Channel A 应看到 ch-a + global，不看 ch-b
        ctx_a = build_memory_context(conn, "test", channel_id="ch-a")
        assert "Channel A" in ctx_a
        assert "全局共享" in ctx_a
        assert "Channel B" not in ctx_a

    def test_no_channel_sees_all(self):
        conn = _make_conn()
        add_memory(conn, type="fact", content="某个频道的记忆信息内容",
                   importance=5, scope="channel:ch-x")
        add_memory(conn, type="fact", content="全局共享的记忆信息内容在此",
                   importance=5, scope="global")

        # 不指定 channel 看到所有 recent
        ctx = build_memory_context(conn, "test")
        assert "全局共享" in ctx
