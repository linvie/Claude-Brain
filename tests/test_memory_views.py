"""brain/memory/views.py 单元测试 — Daily Views 摘要生成器。"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from brain.infra.db import _init_memory_sessions
from brain.memory import views
from brain.memory.store import init_memory_tables


def _make_conn():
    """创建带 memories + memory_sessions 表的内存 DB。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_memory_tables(conn)
    _init_memory_sessions(conn)
    return conn


def _insert_session(conn, session_id, channel_id, opened_at, closed_at, view_generated_at=None):
    """插入测试 session 记录。"""
    conn.execute(
        "INSERT INTO memory_sessions (session_id, channel_id, opened_at, closed_at, view_generated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, channel_id, opened_at, closed_at, view_generated_at),
    )
    conn.commit()


def _insert_memory(conn, content, source, mem_type="fact", importance=5):
    """插入测试记忆。"""
    conn.execute(
        "INSERT INTO memories (type, content, source, tags, importance, last_accessed, created_at, scope) "
        "VALUES (?, ?, ?, '[]', ?, ?, ?, 'global')",
        (mem_type, content, source, importance, int(time.time()), int(time.time())),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _collect_session_info
# ---------------------------------------------------------------------------


class TestCollectSessionInfo:
    def test_with_memories(self):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)
        _insert_memory(conn, "用户偏好 Python 开发", "session:s1", importance=8)

        sessions = conn.execute("SELECT * FROM memory_sessions").fetchall()
        result = views._collect_session_info(conn, sessions)

        assert "ch1" in result
        assert "10:00" in result
        assert "11:00" in result
        assert "Python" in result
        assert "重要度:8" in result

    def test_without_memories(self):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 14, 30, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s2", "ch2", ts, ts + 1800)

        sessions = conn.execute("SELECT * FROM memory_sessions").fetchall()
        result = views._collect_session_info(conn, sessions)

        assert "ch2" in result
        assert "无已提取记忆" in result

    def test_multiple_sessions(self):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)
        _insert_session(conn, "s2", "ch2", ts + 3600, ts + 7200)

        sessions = conn.execute("SELECT * FROM memory_sessions ORDER BY opened_at").fetchall()
        result = views._collect_session_info(conn, sessions)

        assert "ch1" in result
        assert "ch2" in result


# ---------------------------------------------------------------------------
# _write_view_file
# ---------------------------------------------------------------------------


class TestWriteViewFile:
    def test_creates_file(self, tmp_path):
        with patch.object(views, "MEMORY_VIEWS_DIR", tmp_path):
            path = views._write_view_file("2026-04-14", "## Sessions\n- test")

        assert path.exists()
        content = path.read_text()
        assert "# Daily Memory View — 2026-04-14" in content
        assert "## Sessions" in content

    def test_overwrites_existing(self, tmp_path):
        with patch.object(views, "MEMORY_VIEWS_DIR", tmp_path):
            views._write_view_file("2026-04-14", "first version")
            path = views._write_view_file("2026-04-14", "second version")

        content = path.read_text()
        assert "second version" in content
        assert "first version" not in content


# ---------------------------------------------------------------------------
# _mark_sessions_view_generated
# ---------------------------------------------------------------------------


class TestMarkSessionsSummarized:
    def test_marks_all(self):
        conn = _make_conn()
        ts = int(time.time())
        _insert_session(conn, "s1", "ch1", ts - 3600, ts - 1800)
        _insert_session(conn, "s2", "ch2", ts - 3600, ts - 1800)

        sessions = conn.execute("SELECT * FROM memory_sessions").fetchall()
        views._mark_sessions_view_generated(conn, sessions)

        for sid in ("s1", "s2"):
            row = conn.execute(
                "SELECT view_generated_at FROM memory_sessions WHERE session_id = ?",
                (sid,),
            ).fetchone()
            assert row["view_generated_at"] is not None

    def test_empty_list(self):
        conn = _make_conn()
        views._mark_sessions_view_generated(conn, [])  # should not raise


# ---------------------------------------------------------------------------
# generate_daily_view
# ---------------------------------------------------------------------------


class TestGenerateDailyView:
    async def test_no_sessions_returns_none(self):
        conn = _make_conn()
        result = await views.generate_daily_view(conn, date="2026-04-14")
        assert result is None

    async def test_already_summarized_returns_none(self):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600, view_generated_at=ts + 7200)

        result = await views.generate_daily_view(conn, date="2026-04-14")
        assert result is None

    async def test_generates_view(self, tmp_path):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)
        _insert_memory(conn, "用户偏好 Python 开发", "session:s1", importance=8)

        haiku_output = (
            "## Sessions\n"
            "- [ch1] 10:00-11:00: 讨论了开发偏好\n\n"
            "## Key Facts Learned\n"
            "- 用户偏好 Python 开发\n\n"
            "## Decisions Made\n"
            "（无）\n\n"
            "## Open Questions\n"
            "（无）"
        )

        with (
            patch.object(views, "haiku_complete", new=AsyncMock(return_value=haiku_output)),
            patch.object(views, "MEMORY_VIEWS_DIR", tmp_path),
        ):
            result = await views.generate_daily_view(conn, date="2026-04-14")

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "Daily Memory View — 2026-04-14" in content
        assert "Python" in content

        # 验证 view_generated_at 被更新
        row = conn.execute(
            "SELECT view_generated_at FROM memory_sessions WHERE session_id = ?",
            ("s1",),
        ).fetchone()
        assert row["view_generated_at"] is not None

    async def test_haiku_empty_output_returns_none(self):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)

        with patch.object(views, "haiku_complete", new=AsyncMock(return_value="")):
            result = await views.generate_daily_view(conn, date="2026-04-14")

        assert result is None
        # view_generated_at 不应被更新（Haiku 失败，下次重试）
        row = conn.execute(
            "SELECT view_generated_at FROM memory_sessions WHERE session_id = ?",
            ("s1",),
        ).fetchone()
        assert row["view_generated_at"] is None

    async def test_default_date_is_today(self):
        conn = _make_conn()
        # 无 session，应返回 None 但不报错
        result = await views.generate_daily_view(conn)
        assert result is None

    async def test_sessions_without_memories_still_generates(self, tmp_path):
        """session 没有已提取记忆时，仍应尝试生成摘要（会标注无记忆）。"""
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)

        haiku_output = "## Sessions\n- [ch1] 10:00-11:00: 对话\n\n## Key Facts Learned\n（无）\n\n## Decisions Made\n（无）\n\n## Open Questions\n（无）"

        with (
            patch.object(views, "haiku_complete", new=AsyncMock(return_value=haiku_output)),
            patch.object(views, "MEMORY_VIEWS_DIR", tmp_path),
        ):
            result = await views.generate_daily_view(conn, date="2026-04-14")

        assert result is not None


# ---------------------------------------------------------------------------
# run_daily_views_job
# ---------------------------------------------------------------------------


class TestRunDailyViewsJob:
    async def test_no_pending_sessions(self):
        conn = _make_conn()
        await views.run_daily_views_job(conn)  # should not raise

    async def test_processes_multiple_dates(self, tmp_path):
        conn = _make_conn()
        # Day 1: 2026-04-13
        ts1 = int(datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts1, ts1 + 3600)
        # Day 2: 2026-04-14
        ts2 = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s2", "ch2", ts2, ts2 + 3600)

        haiku_output = "## Sessions\n- test\n\n## Key Facts Learned\n（无）\n\n## Decisions Made\n（无）\n\n## Open Questions\n（无）"

        with (
            patch.object(views, "haiku_complete", new=AsyncMock(return_value=haiku_output)),
            patch.object(views, "MEMORY_VIEWS_DIR", tmp_path),
        ):
            await views.run_daily_views_job(conn)

        # 两个 session 都应标记为已摘要
        for sid in ("s1", "s2"):
            row = conn.execute(
                "SELECT view_generated_at FROM memory_sessions WHERE session_id = ?",
                (sid,),
            ).fetchone()
            assert row["view_generated_at"] is not None

        # 应该生成两个 view 文件
        view_files = list(tmp_path.glob("*.md"))
        assert len(view_files) == 2

    async def test_skips_already_summarized(self, tmp_path):
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600, view_generated_at=ts + 7200)

        mock_haiku = AsyncMock(return_value="should not be called")
        with (
            patch.object(views, "haiku_complete", new=mock_haiku),
            patch.object(views, "MEMORY_VIEWS_DIR", tmp_path),
        ):
            await views.run_daily_views_job(conn)

        mock_haiku.assert_not_called()

    async def test_handles_generate_exception(self, tmp_path):
        """generate_daily_view 抛异常时不中断整个 job。"""
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)

        with (
            patch.object(views, "generate_daily_view", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(views, "MEMORY_VIEWS_DIR", tmp_path),
        ):
            await views.run_daily_views_job(conn)  # should not raise


# ---------------------------------------------------------------------------
# _mark_sessions_view_generated exception path
# ---------------------------------------------------------------------------


class TestMarkSessionsSummarizedError:
    def test_handles_db_error(self):
        """DB 异常不应传播。"""
        conn = _make_conn()
        ts = int(time.time())
        _insert_session(conn, "s1", "ch1", ts - 3600, ts - 1800)
        sessions = conn.execute("SELECT * FROM memory_sessions").fetchall()

        # 关闭连接制造异常
        conn.close()
        views._mark_sessions_view_generated(conn, sessions)  # should not raise


# ---------------------------------------------------------------------------
# generate_daily_view: empty session_summaries path
# ---------------------------------------------------------------------------


class TestGenerateDailyViewEmptySummaries:
    async def test_empty_collect_returns_none(self):
        """_collect_session_info 返回空白时提前退出。"""
        conn = _make_conn()
        ts = int(datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc).timestamp())
        _insert_session(conn, "s1", "ch1", ts, ts + 3600)

        with patch.object(views, "_collect_session_info", return_value="   "):
            result = await views.generate_daily_view(conn, date="2026-04-14")

        assert result is None
        # sessions should be marked summarized
        row = conn.execute(
            "SELECT view_generated_at FROM memory_sessions WHERE session_id = ?",
            ("s1",),
        ).fetchone()
        assert row["view_generated_at"] is not None
