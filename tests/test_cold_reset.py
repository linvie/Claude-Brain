"""Tests for Cold 策略 — reset + always-on 记忆注入。

验证：
- cold 状态时 _query_once 调用 _reset_session + _build_memory_append
- _reset_session 先调用 _disconnect，然后清除 session_id
- _build_memory_append 查询 importance >= 阈值的记忆
- 记忆注入 token 预算不超过 ~8000 字符，超出截断
- system_append = 原始模板 + 记忆上下文
- hot/warm 状态不触发 cold reset
"""

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from brain.executor.cc import _MEMORY_INJECT_CHAR_BUDGET, _LiveSession


def _make_session(**kwargs) -> _LiveSession:
    """创建测试用 _LiveSession（不连接 CC）。"""
    return _LiveSession(
        channel_id="test-channel",
        cwd=Path("/tmp"),
        **kwargs,
    )


def _make_memory_db(memories: list[tuple[str, str, int]] | None = None) -> sqlite3.Connection:
    """创建内存 SQLite DB，建 memories 表并插入测试数据。

    Args:
        memories: list of (type, content, importance) tuples
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 5,
            created_at INTEGER NOT NULL DEFAULT 0,
            last_accessed INTEGER,
            tags TEXT,
            scope TEXT DEFAULT 'global'
        )
    """)
    if memories:
        now = int(time.time())
        for i, (mtype, content, importance) in enumerate(memories):
            conn.execute(
                "INSERT INTO memories (type, content, importance, created_at) VALUES (?, ?, ?, ?)",
                (mtype, content, importance, now - i * 60),
            )
    conn.commit()
    return conn


# ── _reset_session ──


class TestResetSession:
    """_reset_session() 单元测试。"""

    async def test_reset_calls_disconnect(self):
        """reset 应先调用 _disconnect。"""
        session = _make_session()
        session._connected = True
        session.session_id = "old-sid"
        session._db_session_id = "old-db-sid"

        with patch.object(session, "_disconnect", new_callable=AsyncMock) as mock_disconnect:
            await session._reset_session()

        mock_disconnect.assert_awaited_once()

    async def test_reset_clears_session_id(self):
        """reset 后 session_id 和 _db_session_id 应为 None。"""
        session = _make_session()
        session._connected = True
        session.session_id = "old-sid"
        session._db_session_id = "old-db-sid"

        with patch.object(session, "_disconnect", new_callable=AsyncMock):
            await session._reset_session()

        assert session.session_id is None
        assert session._db_session_id is None

    async def test_reset_notifies_via_on_stream(self):
        """reset 应通过 on_stream 通知用户。"""
        session = _make_session()
        session._connected = True

        stream_calls = []

        async def on_stream(text):
            stream_calls.append(text)

        with patch.object(session, "_disconnect", new_callable=AsyncMock):
            await session._reset_session(on_stream=on_stream)

        assert len(stream_calls) == 1
        assert "重置" in stream_calls[0]

    async def test_reset_on_stream_failure_does_not_block(self):
        """on_stream 异常不应阻塞 reset 流程。"""
        session = _make_session()
        session._connected = True
        session.session_id = "sid"

        async def bad_stream(text):
            raise RuntimeError("stream broken")

        with patch.object(session, "_disconnect", new_callable=AsyncMock):
            await session._reset_session(on_stream=bad_stream)

        # 不抛异常即表示成功
        assert session.session_id is None

    async def test_reset_works_when_not_connected(self):
        """未连接时 reset 也应正常工作（_disconnect 内部会检查）。"""
        session = _make_session()
        session._connected = False
        session.session_id = "sid"

        with patch.object(session, "_disconnect", new_callable=AsyncMock):
            await session._reset_session()

        assert session.session_id is None


# ── _build_memory_append ──


class TestBuildMemoryAppend:
    """_build_memory_append() 单元测试。"""

    def test_returns_empty_when_memory_disabled(self):
        """MEMORY_ENABLED=False 时应返回空字符串。"""
        session = _make_session()

        with patch("brain.executor.cc.MEMORY_ENABLED", False):
            result = session._build_memory_append()

        assert result == ""

    def test_returns_empty_when_no_memories(self):
        """memories 表为空时应返回空字符串。"""
        session = _make_session()
        conn = _make_memory_db()

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.executor.cc.MEMORY_ALWAYS_ON_THRESHOLD", 8),
            patch("brain.infra.db.get_db", return_value=conn),
        ):
            result = session._build_memory_append()

        assert result == ""

    def test_returns_empty_when_no_high_importance(self):
        """没有 importance >= 8 的记忆时应返回空字符串。"""
        session = _make_session()
        conn = _make_memory_db([
            ("fact", "low importance fact", 5),
            ("preference", "medium preference", 7),
        ])

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.executor.cc.MEMORY_ALWAYS_ON_THRESHOLD", 8),
            patch("brain.infra.db.get_db", return_value=conn),
        ):
            result = session._build_memory_append()

        assert result == ""

    def test_returns_formatted_memories(self):
        """应返回格式化的 always-on 记忆。"""
        session = _make_session()
        conn = _make_memory_db([
            ("preference", "用户喜欢简洁回复", 9),
            ("decision", "项目使用 Python 3.12", 8),
            ("fact", "low importance", 5),  # 不应包含
        ])

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.executor.cc.MEMORY_ALWAYS_ON_THRESHOLD", 8),
            patch("brain.infra.db.get_db", return_value=conn),
        ):
            result = session._build_memory_append()

        assert "用户记忆" in result
        assert "[preference] 用户喜欢简洁回复" in result
        assert "[decision] 项目使用 Python 3.12" in result
        assert "low importance" not in result

    def test_memories_ordered_by_importance_desc(self):
        """记忆应按 importance 降序排列。"""
        session = _make_session()
        conn = _make_memory_db([
            ("fact", "importance 8", 8),
            ("preference", "importance 10", 10),
            ("decision", "importance 9", 9),
        ])

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.executor.cc.MEMORY_ALWAYS_ON_THRESHOLD", 8),
            patch("brain.infra.db.get_db", return_value=conn),
        ):
            result = session._build_memory_append()

        # importance 10 应在 9 之前，9 在 8 之前
        idx_10 = result.index("importance 10")
        idx_9 = result.index("importance 9")
        idx_8 = result.index("importance 8")
        assert idx_10 < idx_9 < idx_8

    def test_truncates_at_char_budget(self):
        """超过字符预算时应截断。"""
        session = _make_session()
        # 创建大量记忆以超过预算
        memories = [
            ("fact", f"这是一条很长的记忆内容，编号 {i}，用于测试截断功能。" * 20, 9)
            for i in range(50)
        ]
        conn = _make_memory_db(memories)

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.executor.cc.MEMORY_ALWAYS_ON_THRESHOLD", 8),
            patch("brain.infra.db.get_db", return_value=conn),
        ):
            result = session._build_memory_append()

        assert len(result) <= _MEMORY_INJECT_CHAR_BUDGET + 200  # 允许截断提示的余量
        assert "截断" in result

    def test_db_error_returns_empty(self):
        """数据库查询异常时应返回空字符串（不阻塞）。"""
        session = _make_session()

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.infra.db.get_db", side_effect=RuntimeError("DB error")),
        ):
            result = session._build_memory_append()

        assert result == ""


# ── Cold 策略集成（_query_once 中的 cold 分支）──


class TestColdResetInQueryOnce:
    """_query_once() 中 cold reset 集成测试。"""

    async def test_cold_triggers_reset_and_memory_inject(self):
        """cold 状态时应调用 _reset_session + _build_memory_append。"""
        session = _make_session(system_append="original template")
        session.last_activity = 0  # cold: never active

        call_order = []

        async def mock_reset(on_stream=None):
            call_order.append("reset")
            session._connected = False

        def mock_memory():
            call_order.append("memory")
            return "\n\n## 用户记忆\n- [pref] test"

        async def mock_ensure(resume=None):
            call_order.append(f"connect:resume={resume}")
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "new-sid"
            mock_result.result = "response"
            mock_result.total_cost_usd = 0.01
            mock_result.duration_ms = 100
            mock_result.num_turns = 1

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", side_effect=mock_memory),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            mock_result_type = type(MagicMock())
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                sid, text, meta = await session._query_once("hello")

        # 验证调用顺序：先 ensure_connected → cold 检测 → reset → memory → reconnect
        assert "reset" in call_order
        assert "memory" in call_order
        # reset 后 reconnect 不带 resume（全新 session）
        assert "connect:resume=None" in call_order

    async def test_cold_appends_memory_to_system_append(self):
        """cold reset 应将记忆追加到 _system_append。"""
        session = _make_session(system_append="原始模板内容")
        session.last_activity = 0  # cold

        memory_text = "\n\n## 用户记忆\n- [preference] 简洁回复"

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "new-sid"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", return_value=memory_text),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            mock_result_type = type(MagicMock())
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test")

        assert "原始模板内容" in session._system_append
        assert "用户记忆" in session._system_append
        assert "简洁回复" in session._system_append

    async def test_cold_no_memory_does_not_modify_system_append(self):
        """无记忆时 system_append 不变。"""
        original = "原始模板"
        session = _make_session(system_append=original)
        session.last_activity = 0

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", return_value=""),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            mock_result_type = type(MagicMock())
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test")

        assert session._system_append == original

    async def test_cold_passes_on_stream_to_reset(self):
        """cold reset 应将 on_stream 传递给 _reset_session。"""
        session = _make_session()
        session.last_activity = 0

        captured_on_stream = []

        async def mock_reset(on_stream=None):
            captured_on_stream.append(on_stream)
            session._connected = False

        async def mock_ensure(resume=None):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

        async def my_stream(text):
            pass

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", return_value=""),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            mock_result_type = type(MagicMock())
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test", on_stream=my_stream)

        assert len(captured_on_stream) == 1
        assert captured_on_stream[0] is my_stream

    async def test_hot_does_not_trigger_cold_reset(self):
        """hot 状态不应触发 cold reset。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot

        with (
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

            mock_result_type = type(mock_result)
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test")

        mock_reset.assert_not_awaited()

    async def test_warm_does_not_trigger_cold_reset(self):
        """warm 状态不应触发 cold reset。"""
        session = _make_session()
        session.last_activity = time.time() - 3700  # warm (约 62 min)

        with (
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
            patch.object(session, "_compact_session", new_callable=AsyncMock),
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 3600),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 14400),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

            mock_result_type = type(mock_result)
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test")

        mock_reset.assert_not_awaited()

    async def test_cold_reconnect_failure_falls_back(self):
        """cold reset 后重连失败应 fallback。"""
        session = _make_session()
        session.last_activity = 0  # cold

        connect_count = 0

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            nonlocal connect_count
            connect_count += 1
            if connect_count == 1:
                # 第一次 ensure_connected 成功（初始连接）
                session._connected = True
            else:
                # cold reset 后的重连失败
                raise RuntimeError("reconnect failed")

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", return_value=""),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch.object(session, "_fallback_query", new_callable=AsyncMock, return_value=(None, "fallback", {})) as mock_fallback,
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            sid, text, meta = await session._query_once("test")

        mock_fallback.assert_awaited_once()
        assert text == "fallback"
