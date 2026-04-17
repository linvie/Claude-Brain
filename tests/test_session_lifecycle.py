"""三层 session 策略集成测试。

覆盖端到端场景：
- hot 直接复用（不触发 compact/reset）
- warm 触发 compact 后再 query
- cold 触发 reset + 记忆注入后再 query
- context 超限强制 compact（高于温度策略优先级）
- compact 失败降级（不阻塞 query）
- 配置默认值正确
- 温度判断边界值
- 策略之间的互斥性
"""

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain.executor.cc import _LiveSession, _MEMORY_INJECT_CHAR_BUDGET


# ── Helpers ──


def _make_session(**kwargs) -> _LiveSession:
    """创建测试用 _LiveSession（不连接 CC）。"""
    return _LiveSession(
        channel_id="lifecycle-test",
        cwd=Path("/tmp"),
        **kwargs,
    )


def _make_result_msg(**overrides) -> MagicMock:
    """创建通过 isinstance 检查的 ResultMessage mock。"""
    from claude_agent_sdk import ResultMessage

    defaults = {
        "session_id": "sid-lifecycle",
        "result": "ok",
        "total_cost_usd": 0.01,
        "duration_ms": 100,
        "num_turns": 1,
        "usage": {"input_tokens": 50000},
    }
    defaults.update(overrides)
    msg = MagicMock(spec=ResultMessage)
    for k, v in defaults.items():
        setattr(msg, k, v)
    return msg


def _make_memory_db(memories: list[tuple[str, str, int]] | None = None) -> sqlite3.Connection:
    """创建内存 SQLite DB 并插入测试记忆数据。"""
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


def _setup_connected_session(session: _LiveSession, result_msg: MagicMock):
    """设置 session 的 mock client 使其可以完成一次 query。"""
    session._connected = True
    mock_client = AsyncMock()

    async def mock_receive():
        yield result_msg

    mock_client.receive_response = mock_receive
    session.client = mock_client
    return mock_client


# ── 1. Hot 直接复用 ──


class TestHotSessionReuse:
    """hot 温度：直接复用 session，不触发任何策略。"""

    async def test_hot_skips_compact_and_reset(self):
        """hot 状态下不触发 compact 和 reset。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # 1 秒前活动 → hot

        result_msg = _make_result_msg()

        with (
            patch.object(session, "_compact_session", new_callable=AsyncMock) as mock_compact,
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)

            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                sid, text, meta = await session._query_once("hello")

        mock_compact.assert_not_awaited()
        mock_reset.assert_not_awaited()
        assert sid == "sid-lifecycle"
        assert text == "ok"

    async def test_hot_returns_response_directly(self):
        """hot 状态下直接返回 CC 响应。"""
        session = _make_session()
        session.last_activity = time.time() - 10  # 10 秒前 → hot

        result_msg = _make_result_msg(result="direct response", total_cost_usd=0.05)

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                sid, text, meta = await session._query_once("test")

        assert text == "direct response"
        assert meta["total_cost_usd"] == 0.05


# ── 2. Warm 触发 compact ──


class TestWarmCompactIntegration:
    """warm 温度：query 前自动 compact，降低 cache write 成本。"""

    async def test_warm_calls_compact_before_user_query(self):
        """warm 状态下先调用 _compact_session，然后发送用户消息。"""
        session = _make_session()
        session.last_activity = time.time() - 360  # 6 分钟前 → warm

        call_order = []

        async def tracked_compact(on_stream=None):
            call_order.append("compact")
            return True

        result_msg = _make_result_msg()

        async def tracked_ensure(resume=None):
            session._connected = True
            mock_client = AsyncMock()

            async def mock_receive():
                yield result_msg

            mock_client.receive_response = mock_receive

            original_query = mock_client.query

            async def tracked_query(prompt):
                call_order.append(f"query:{prompt}")
                return await original_query(prompt)

            mock_client.query = tracked_query
            session.client = mock_client

        with (
            patch.object(session, "_compact_session", side_effect=tracked_compact),
            patch.object(session, "_ensure_connected", side_effect=tracked_ensure),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                await session._query_once("user message")

        assert "compact" in call_order
        query_entries = [c for c in call_order if c.startswith("query:")]
        assert any("user message" in q for q in query_entries)
        # compact 在 user query 之前
        compact_idx = call_order.index("compact")
        query_idx = next(i for i, c in enumerate(call_order) if "user message" in c)
        assert compact_idx < query_idx

    async def test_warm_does_not_trigger_reset(self):
        """warm 状态下不触发 cold reset。"""
        session = _make_session()
        session.last_activity = time.time() - 600  # 10 分钟前 → warm

        result_msg = _make_result_msg()

        with (
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=True),
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                await session._query_once("test")

        mock_reset.assert_not_awaited()


# ── 3. Cold 触发 reset + 记忆注入 ──


class TestColdResetIntegration:
    """cold 温度：reset 旧 session + 注入 always-on 记忆 + 创建新 session。"""

    async def test_cold_full_flow(self):
        """cold 完整流程：reset → 记忆注入 → 新 session → query。"""
        session = _make_session(system_append="base template")
        session.last_activity = 0  # 从未活动 → cold

        call_order = []

        async def mock_reset(on_stream=None):
            call_order.append("reset")
            session._connected = False

        def mock_memory():
            call_order.append("memory_inject")
            return "\n\n## 用户记忆\n- [pref] likes concise"

        connect_count = 0

        async def mock_ensure(resume=None):
            nonlocal connect_count
            connect_count += 1
            call_order.append(f"connect:{resume}")
            session._connected = True
            result_msg = _make_result_msg(session_id="new-sid")

            mock_client = AsyncMock()

            async def mock_receive():
                yield result_msg

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
                sid, text, meta = await session._query_once("hello after cold")

        # 验证调用顺序
        assert "reset" in call_order
        assert "memory_inject" in call_order
        # reset 后重连不带 resume
        assert "connect:None" in call_order
        # 记忆被追加到 system_append
        assert "base template" in session._system_append
        assert "用户记忆" in session._system_append

    async def test_cold_no_memories_system_append_unchanged(self):
        """cold 但无 always-on 记忆时，system_append 不变。"""
        original = "原始模板"
        session = _make_session(system_append=original)
        session.last_activity = 0  # cold

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            session._connected = True
            result_msg = _make_result_msg()
            _setup_connected_session(session, result_msg)

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

    async def test_cold_does_not_trigger_compact(self):
        """cold 状态下不触发 warm compact。"""
        session = _make_session()
        session.last_activity = 0  # cold

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            session._connected = True
            result_msg = _make_result_msg()
            _setup_connected_session(session, result_msg)

        with (
            patch.object(session, "_compact_session", new_callable=AsyncMock) as mock_compact,
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", return_value=""),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            mock_result_type = type(MagicMock())
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test")

        mock_compact.assert_not_awaited()

    async def test_cold_memory_injection_with_real_db(self):
        """cold reset 从真实 DB 查询并注入 always-on 记忆。"""
        session = _make_session(system_append="template")
        session.last_activity = 0

        conn = _make_memory_db([
            ("preference", "喜欢简洁回复", 9),
            ("decision", "使用 Python 3.12", 8),
            ("fact", "低重要性信息", 5),  # importance < 8, 不应注入
        ])

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            session._connected = True
            result_msg = _make_result_msg()
            _setup_connected_session(session, result_msg)

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch("brain.executor.cc.MEMORY_ALWAYS_ON_THRESHOLD", 8),
            patch("brain.infra.db.get_db", return_value=conn),
        ):
            mock_result_type = type(MagicMock())
            with patch("brain.executor.cc.ResultMessage", mock_result_type):
                await session._query_once("test")

        assert "喜欢简洁回复" in session._system_append
        assert "使用 Python 3.12" in session._system_append
        assert "低重要性信息" not in session._system_append


# ── 4. Context 超限强制 compact ──


class TestContextExceedForceCompact:
    """context tokens 超限时强制 compact，优先级高于温度策略。"""

    async def test_force_compact_overrides_hot(self):
        """即使温度 hot，超限也触发 compact 而非跳过。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot
        session.last_context_tokens = 250000  # 超过 200000

        result_msg = _make_result_msg(usage={"input_tokens": 50000})

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(
                session, "_compact_session", new_callable=AsyncMock, return_value=True
            ) as mock_compact,
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    sid, text, meta = await session._query_once("test")

        mock_compact.assert_awaited_once()
        mock_reset.assert_not_awaited()

    async def test_force_compact_overrides_cold(self):
        """超限时走 compact 路径，不走 cold reset（安全网优先）。"""
        session = _make_session()
        session.last_activity = 0  # cold
        session.last_context_tokens = 300000  # 超限
        session._connected = True

        result_msg = _make_result_msg(usage={"input_tokens": 30000})

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(
                session, "_compact_session", new_callable=AsyncMock, return_value=True
            ) as mock_compact,
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    await session._query_once("test")

        mock_compact.assert_awaited_once()
        mock_reset.assert_not_awaited()

    async def test_force_compact_resets_token_count_on_success(self):
        """compact 成功后 last_context_tokens 归零（等下次 query 重新计算）。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot
        session.last_context_tokens = 250000

        # compact 后的 query 返回新的 token 数
        result_msg = _make_result_msg(usage={"input_tokens": 40000})

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=True),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    await session._query_once("test")

        # compact 归零后被新 query 的 usage 更新
        assert session.last_context_tokens == 40000

    async def test_no_force_compact_when_under_limit(self):
        """未超限时不触发强制 compact。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot
        session.last_context_tokens = 100000  # 低于 200000

        result_msg = _make_result_msg()

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock) as mock_compact,
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    await session._query_once("test")

        mock_compact.assert_not_awaited()


# ── 5. Compact 失败降级 ──


class TestCompactFailureDegradation:
    """compact 失败时不阻塞用户 query。"""

    async def test_warm_compact_failure_still_queries(self):
        """warm compact 失败后，用户消息仍正常发送。"""
        session = _make_session()
        session.last_activity = time.time() - 360  # warm

        result_msg = _make_result_msg(result="response despite compact fail")

        async def failing_compact(on_stream=None):
            return False

        with (
            patch.object(session, "_compact_session", side_effect=failing_compact),
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                sid, text, meta = await session._query_once("hello")

        assert text == "response despite compact fail"

    async def test_force_compact_failure_still_queries(self):
        """强制 compact 失败后，用户 query 仍继续执行。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot
        session.last_context_tokens = 250000  # 超限

        # compact 失败，但 query 仍执行并返回新 token 数
        result_msg = _make_result_msg(result="still works", usage={"input_tokens": 250000})

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=False),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    sid, text, meta = await session._query_once("test")

        assert text == "still works"
        # compact 失败不阻塞 query，token 数由新 query 的 usage 更新
        assert session.last_context_tokens == 250000

    async def test_cold_reconnect_failure_falls_back(self):
        """cold reset 后重连失败时走 fallback 路径。"""
        session = _make_session()
        session.last_activity = 0  # cold

        connect_count = 0

        async def mock_reset(on_stream=None):
            session._connected = False

        async def mock_ensure(resume=None):
            nonlocal connect_count
            connect_count += 1
            if connect_count == 1:
                session._connected = True
            else:
                raise RuntimeError("reconnect failed")

        with (
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch.object(session, "_build_memory_append", return_value=""),
            patch.object(session, "_ensure_connected", side_effect=mock_ensure),
            patch.object(
                session, "_fallback_query",
                new_callable=AsyncMock, return_value=(None, "fallback response", {}),
            ) as mock_fallback,
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            sid, text, meta = await session._query_once("test")

        mock_fallback.assert_awaited_once()
        assert text == "fallback response"


# ── 6. 配置默认值 ──


class TestSessionConfigDefaults:
    """session 相关配置项默认值正确。"""

    def test_warm_threshold_default(self):
        from brain.config import SESSION_WARM_THRESHOLD
        assert SESSION_WARM_THRESHOLD == 300  # 5 min * 60

    def test_reset_threshold_default(self):
        from brain.config import SESSION_RESET_THRESHOLD
        assert SESSION_RESET_THRESHOLD == 7200  # 2 hr * 3600

    def test_max_context_tokens_default(self):
        from brain.config import SESSION_MAX_CONTEXT_TOKENS
        assert SESSION_MAX_CONTEXT_TOKENS == 200000

    def test_idle_timeout_default(self):
        from brain.config import SESSION_IDLE_TIMEOUT
        assert SESSION_IDLE_TIMEOUT == 600  # 10 min

    def test_memory_always_on_threshold_default(self):
        from brain.config import MEMORY_ALWAYS_ON_THRESHOLD
        assert MEMORY_ALWAYS_ON_THRESHOLD == 8

    def test_memory_inject_char_budget(self):
        assert _MEMORY_INJECT_CHAR_BUDGET == 8000


# ── 7. 温度判断边界值 ──


class TestTemperatureBoundaries:
    """温度判断的边界条件集成验证。"""

    def test_never_active_is_cold(self):
        session = _make_session()
        assert session._get_session_temperature() == "cold"

    @patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300)
    def test_exactly_at_warm_boundary(self):
        """恰好 warm_threshold 秒 → warm（含边界）。"""
        session = _make_session()
        session.last_activity = time.time() - 300
        assert session._get_session_temperature() == "warm"

    @patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300)
    def test_one_second_below_warm_is_hot(self):
        """warm_threshold - 1 秒 → hot。"""
        session = _make_session()
        session.last_activity = time.time() - 299
        assert session._get_session_temperature() == "hot"

    @patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200)
    def test_exactly_at_cold_boundary(self):
        """恰好 reset_threshold 秒 → cold（含边界）。"""
        session = _make_session()
        session.last_activity = time.time() - 7200
        assert session._get_session_temperature() == "cold"

    @patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300)
    @patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200)
    def test_one_second_below_cold_is_warm(self):
        """reset_threshold - 1 秒 → warm。"""
        session = _make_session()
        session.last_activity = time.time() - 7199
        assert session._get_session_temperature() == "warm"


# ── 8. 策略互斥性 ──


class TestStrategyMutualExclusion:
    """三层策略之间的互斥性验证。"""

    async def test_only_one_strategy_executes_per_query(self):
        """每次 query 最多只有一种策略执行。"""
        strategies = {"compact": 0, "reset": 0}

        session = _make_session()
        session.last_activity = time.time() - 360  # warm

        async def count_compact(on_stream=None):
            strategies["compact"] += 1
            return True

        async def count_reset(on_stream=None):
            strategies["reset"] += 1
            session._connected = False

        result_msg = _make_result_msg()

        with (
            patch.object(session, "_compact_session", side_effect=count_compact),
            patch.object(session, "_reset_session", side_effect=count_reset),
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                await session._query_once("test")

        # warm: 只 compact 不 reset
        assert strategies["compact"] == 1
        assert strategies["reset"] == 0

    async def test_force_compact_skips_temperature_strategy(self):
        """强制 compact 后不再执行温度策略。"""
        strategies = {"force_compact": 0, "warm_compact": 0, "cold_reset": 0}

        session = _make_session()
        session.last_activity = time.time() - 360  # warm
        session.last_context_tokens = 250000  # 超限

        async def mock_compact(on_stream=None):
            strategies["force_compact"] += 1
            return True

        async def mock_reset(on_stream=None):
            strategies["cold_reset"] += 1

        result_msg = _make_result_msg(usage={"input_tokens": 50000})

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", side_effect=mock_compact),
            patch.object(session, "_reset_session", side_effect=mock_reset),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    await session._query_once("test")

        # 强制 compact 执行 1 次，温度策略不再执行
        assert strategies["force_compact"] == 1
        assert strategies["cold_reset"] == 0


# ── 9. last_activity 更新 ──


class TestLastActivityUpdate:
    """query 完成后 last_activity 被更新。"""

    async def test_last_activity_updated_after_query(self):
        """query 结束后 last_activity 应更新为当前时间。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot
        before = time.time()

        result_msg = _make_result_msg()

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                await session._query_once("test")

        assert session.last_activity >= before


# ── 10. Token 追踪端到端 ──


class TestTokenTrackingEndToEnd:
    """从 ResultMessage.usage 到 last_context_tokens 的端到端验证。"""

    async def test_token_count_updated_from_usage(self):
        """query 完成后 last_context_tokens 反映最新 usage.input_tokens。"""
        session = _make_session()
        session.last_activity = time.time() - 1  # hot
        session.last_context_tokens = 0

        result_msg = _make_result_msg(usage={"input_tokens": 120000})

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            _setup_connected_session(session, result_msg)
            with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                with patch.object(session, "_record_message_count"):
                    await session._query_once("test")

        assert session.last_context_tokens == 120000

    async def test_tokens_accumulate_across_queries(self):
        """连续 query 后 token 数应反映最新值（非累加）。"""
        session = _make_session()
        session.last_activity = time.time() - 1

        for expected_tokens in [50000, 80000, 120000]:
            result_msg = _make_result_msg(usage={"input_tokens": expected_tokens})

            with (
                patch.object(session, "_ensure_connected", new_callable=AsyncMock),
                patch("brain.executor.cc.MEMORY_ENABLED", False),
            ):
                _setup_connected_session(session, result_msg)
                with patch("brain.executor.cc.ResultMessage", type(result_msg)):
                    with patch.object(session, "_record_message_count"):
                        await session._query_once("test")
                        session.last_activity = time.time()

            assert session.last_context_tokens == expected_tokens
