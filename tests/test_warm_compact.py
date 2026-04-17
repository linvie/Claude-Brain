"""Tests for Warm 策略 — query 前自动 compact。

验证：
- warm 状态时 _query_once 在发送用户消息前调用 _compact_session
- hot 状态时跳过 compact
- cold 状态时跳过 compact
- compact 失败不阻塞用户消息
- _compact_session 通过 client.query("/compact") 发送指令
- compact 完成后更新 last_activity
- compact 期间通过 on_stream 通知用户
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain.executor.cc import _LiveSession


def _make_session(**kwargs) -> _LiveSession:
    """创建测试用 _LiveSession（不连接 CC）。"""
    return _LiveSession(
        channel_id="test-channel",
        cwd=Path("/tmp"),
        **kwargs,
    )


class TestCompactSession:
    """_compact_session() 单元测试。"""

    async def test_compact_sends_slash_compact(self):
        """应通过 client.query('/compact') 发送压缩指令。"""
        session = _make_session()
        session._connected = True

        mock_client = AsyncMock()
        # receive_response 返回一个包含 ResultMessage 的异步迭代器
        mock_result = MagicMock()
        mock_result.session_id = "sid-1"

        async def mock_receive():
            yield mock_result

        mock_client.receive_response = mock_receive
        session.client = mock_client

        with patch("brain.executor.cc.ResultMessage", type(mock_result)):
            result = await session._compact_session()

        assert result is True
        mock_client.query.assert_awaited_once_with("/compact")

    async def test_compact_updates_last_activity(self):
        """compact 成功后应更新 last_activity。"""
        session = _make_session()
        session._connected = True
        session.last_activity = 100.0

        mock_client = AsyncMock()
        mock_result = MagicMock()

        async def mock_receive():
            yield mock_result

        mock_client.receive_response = mock_receive
        session.client = mock_client

        with patch("brain.executor.cc.ResultMessage", type(mock_result)):
            await session._compact_session()

        assert session.last_activity > 100.0

    async def test_compact_notifies_via_on_stream(self):
        """compact 期间应通过 on_stream 回调通知用户。"""
        session = _make_session()
        session._connected = True

        mock_client = AsyncMock()
        mock_result = MagicMock()

        async def mock_receive():
            yield mock_result

        mock_client.receive_response = mock_receive
        session.client = mock_client

        stream_calls = []

        async def on_stream(text):
            stream_calls.append(text)

        with patch("brain.executor.cc.ResultMessage", type(mock_result)):
            await session._compact_session(on_stream=on_stream)

        assert len(stream_calls) == 1
        assert "整理上下文" in stream_calls[0]

    async def test_compact_returns_false_when_not_connected(self):
        """未连接时应返回 False。"""
        session = _make_session()
        session._connected = False
        session.client = None

        result = await session._compact_session()
        assert result is False

    async def test_compact_returns_false_on_exception(self):
        """client.query 抛异常应返回 False（降级跳过）。"""
        session = _make_session()
        session._connected = True

        mock_client = AsyncMock()
        mock_client.query.side_effect = RuntimeError("compact failed")
        session.client = mock_client

        result = await session._compact_session()
        assert result is False

    async def test_compact_on_stream_failure_does_not_block(self):
        """on_stream 回调异常不应阻塞 compact 流程。"""
        session = _make_session()
        session._connected = True

        mock_client = AsyncMock()
        mock_result = MagicMock()

        async def mock_receive():
            yield mock_result

        mock_client.receive_response = mock_receive
        session.client = mock_client

        async def bad_stream(text):
            raise RuntimeError("stream broken")

        with patch("brain.executor.cc.ResultMessage", type(mock_result)):
            result = await session._compact_session(on_stream=bad_stream)

        assert result is True  # compact 仍然成功


class TestWarmCompactInQueryOnce:
    """_query_once() 中 warm compact 集成测试。"""

    async def test_warm_triggers_compact_before_query(self):
        """warm 状态时应在发送用户消息前调用 _compact_session。"""
        session = _make_session()
        # 设置为 warm 状态（6 分钟前活动）
        session.last_activity = time.time() - 360

        call_order = []

        async def mock_compact(on_stream=None):
            call_order.append("compact")
            return True

        async def mock_ensure_connected(resume=None):
            session._connected = True
            session.client = AsyncMock()

        # mock _query_once 内部调用的 client.query 和 receive_response
        mock_result = MagicMock()
        mock_result.session_id = "sid-1"
        mock_result.result = "response"
        mock_result.total_cost_usd = 0.01
        mock_result.duration_ms = 100
        mock_result.num_turns = 1

        async def mock_receive():
            yield mock_result

        with (
            patch.object(session, "_compact_session", side_effect=mock_compact) as compact_mock,
            patch.object(session, "_ensure_connected", side_effect=mock_ensure_connected),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            # 需要设置 client 在 ensure_connected 后
            async def setup_client(resume=None):
                session._connected = True
                mock_client = AsyncMock()
                mock_client.receive_response = mock_receive

                # 追踪 client.query 调用
                original_query = mock_client.query

                async def tracked_query(prompt):
                    call_order.append(f"query:{prompt}")
                    return await original_query(prompt)

                mock_client.query = tracked_query
                session.client = mock_client

            with patch.object(session, "_ensure_connected", side_effect=setup_client):
                from brain.executor.cc import ResultMessage as RM
                with patch("brain.executor.cc.ResultMessage", type(mock_result)):
                    await session._query_once("hello user")

        # compact 应在 user query 之前
        assert "compact" in call_order
        assert any("hello user" in c for c in call_order)
        compact_idx = call_order.index("compact")
        query_idx = next(i for i, c in enumerate(call_order) if "hello user" in c)
        assert compact_idx < query_idx

    async def test_hot_skips_compact(self):
        """hot 状态时不应触发 compact。"""
        session = _make_session()
        # 设置为 hot 状态（1 秒前活动）
        session.last_activity = time.time() - 1

        with (
            patch.object(session, "_compact_session", new_callable=AsyncMock) as compact_mock,
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            # 设置已连接的 mock client
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid-1"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

            from brain.executor.cc import ResultMessage
            with patch("brain.executor.cc.ResultMessage", type(mock_result)):
                await session._query_once("test")

        compact_mock.assert_not_awaited()

    async def test_cold_skips_compact(self):
        """cold 状态时不应触发 compact（cold 策略由后续 task 实现）。"""
        session = _make_session()
        # 设置为 cold 状态（从未活动）
        session.last_activity = 0

        with (
            patch.object(session, "_compact_session", new_callable=AsyncMock) as compact_mock,
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid-1"
            mock_result.result = "ok"
            mock_result.total_cost_usd = 0
            mock_result.duration_ms = 0
            mock_result.num_turns = 0

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

            with patch("brain.executor.cc.ResultMessage", type(mock_result)):
                await session._query_once("test")

        compact_mock.assert_not_awaited()

    async def test_compact_failure_does_not_block_query(self):
        """compact 失败后应继续正常发送用户消息。"""
        session = _make_session()
        # warm 状态
        session.last_activity = time.time() - 360

        async def failing_compact(on_stream=None):
            return False  # compact 失败

        with (
            patch.object(session, "_compact_session", side_effect=failing_compact),
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid-1"
            mock_result.result = "user response"
            mock_result.total_cost_usd = 0.01
            mock_result.duration_ms = 100
            mock_result.num_turns = 1

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive
            session.client = mock_client

            with patch("brain.executor.cc.ResultMessage", type(mock_result)):
                sid, text, meta = await session._query_once("hello")

        # 即使 compact 失败，用户消息仍应正常发送
        assert sid == "sid-1"
        assert text == "user response"
        mock_client.query.assert_awaited_once_with("hello")

    async def test_warm_compact_passes_on_stream(self):
        """warm compact 应将 on_stream 回调传递给 _compact_session。"""
        session = _make_session()
        session.last_activity = time.time() - 360

        captured_on_stream = []

        async def mock_compact(on_stream=None):
            captured_on_stream.append(on_stream)
            return True

        with (
            patch.object(session, "_compact_session", side_effect=mock_compact),
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch("brain.executor.cc.SESSION_WARM_THRESHOLD", 300),
            patch("brain.executor.cc.SESSION_RESET_THRESHOLD", 7200),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
        ):
            session._connected = True
            mock_client = AsyncMock()
            mock_result = MagicMock()
            mock_result.session_id = "sid-1"
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

            with patch("brain.executor.cc.ResultMessage", type(mock_result)):
                await session._query_once("test", on_stream=my_stream)

        assert len(captured_on_stream) == 1
        assert captured_on_stream[0] is my_stream
