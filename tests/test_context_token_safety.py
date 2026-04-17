"""Context token 追踪 + 超限自动 compact 安全网测试。

覆盖场景：
- last_context_tokens 初始值为 0
- _update_context_tokens 从 usage.input_tokens 读取
- _update_context_tokens 无 usage 时走 JSONL 文件大小估算
- _estimate_tokens_from_jsonl 计算逻辑
- 超限时强制 compact（不管温度）
- hot 温度下超限仍触发 compact
- 未超限时不触发强制 compact
- compact 成功后 last_context_tokens 归零
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import ResultMessage

from brain.executor import cc
from brain.executor.cc import _LiveSession


def _make_result_msg(**overrides) -> MagicMock:
    """创建一个通过 isinstance(msg, ResultMessage) 的 mock。"""
    defaults = {
        "session_id": "sid-1",
        "result": "ok",
        "total_cost_usd": 0,
        "duration_ms": 0,
        "num_turns": 0,
        "usage": {"input_tokens": 10000},
    }
    defaults.update(overrides)
    msg = MagicMock(spec=ResultMessage)
    for k, v in defaults.items():
        setattr(msg, k, v)
    return msg


@pytest.fixture(autouse=True)
def clean_state():
    cc._sessions.clear()
    cc._model_overrides.clear()
    yield
    cc._sessions.clear()
    cc._model_overrides.clear()


class TestLastContextTokensInit:
    """last_context_tokens 初始值。"""

    def test_initial_value_is_zero(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        assert session.last_context_tokens == 0


class TestUpdateContextTokens:
    """_update_context_tokens: 从 ResultMessage 读取 token 数。"""

    def test_reads_input_tokens_from_usage(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        msg = MagicMock()
        msg.usage = {"input_tokens": 150000, "output_tokens": 2000}
        session._update_context_tokens(msg)
        assert session.last_context_tokens == 150000

    def test_usage_none_falls_to_jsonl_estimate(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        msg = MagicMock()
        msg.usage = None
        with patch.object(session, "_estimate_tokens_from_jsonl", return_value=30000):
            session._update_context_tokens(msg)
        assert session.last_context_tokens == 30000

    def test_usage_empty_dict_falls_to_jsonl_estimate(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        msg = MagicMock()
        msg.usage = {}
        with patch.object(session, "_estimate_tokens_from_jsonl", return_value=12000):
            session._update_context_tokens(msg)
        assert session.last_context_tokens == 12000

    def test_usage_input_tokens_zero_falls_to_jsonl(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        msg = MagicMock()
        msg.usage = {"input_tokens": 0}
        with patch.object(session, "_estimate_tokens_from_jsonl", return_value=5000):
            session._update_context_tokens(msg)
        assert session.last_context_tokens == 5000

    def test_no_usage_no_jsonl_stays_zero(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        session.last_context_tokens = 0
        msg = MagicMock()
        msg.usage = None
        with patch.object(session, "_estimate_tokens_from_jsonl", return_value=0):
            session._update_context_tokens(msg)
        assert session.last_context_tokens == 0

    def test_input_tokens_converted_to_int(self):
        """确保浮点数被转换为 int。"""
        session = _LiveSession("ch1", Path("/tmp"), "")
        msg = MagicMock()
        msg.usage = {"input_tokens": 99999.7}
        session._update_context_tokens(msg)
        assert session.last_context_tokens == 99999
        assert isinstance(session.last_context_tokens, int)


class TestEstimateTokensFromJsonl:
    """_estimate_tokens_from_jsonl: JSONL 文件大小 * 0.3 估算。"""

    def test_estimates_from_file_size(self, tmp_path):
        session = _LiveSession("ch1", Path("/tmp"), "")
        # 创建一个 10000 字节的假 JSONL
        fake_jsonl = tmp_path / "test.jsonl"
        fake_jsonl.write_bytes(b"x" * 10000)
        with patch.object(session, "_find_sdk_jsonl", return_value=fake_jsonl):
            result = session._estimate_tokens_from_jsonl()
        assert result == 3000  # 10000 * 0.3

    def test_no_jsonl_returns_zero(self):
        session = _LiveSession("ch1", Path("/tmp"), "")
        with patch.object(session, "_find_sdk_jsonl", return_value=None):
            result = session._estimate_tokens_from_jsonl()
        assert result == 0

    def test_nonexistent_jsonl_returns_zero(self, tmp_path):
        session = _LiveSession("ch1", Path("/tmp"), "")
        missing = tmp_path / "missing.jsonl"
        with patch.object(session, "_find_sdk_jsonl", return_value=missing):
            result = session._estimate_tokens_from_jsonl()
        assert result == 0


class TestForceCompactOnExceed:
    """超限强制 compact：last_context_tokens > max_context_tokens。"""

    async def test_force_compact_when_over_limit(self):
        """超限时应触发 _compact_session，不管温度。"""
        session = _LiveSession("ch1", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.last_context_tokens = 250000
        session.last_activity = 1

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=True) as mock_compact,
            patch.object(session, "_get_session_temperature", return_value="hot"),
        ):
            session.client.query = AsyncMock()
            result_msg = _make_result_msg(total_cost_usd=0.01, usage={"input_tokens": 50000})

            async def mock_receive():
                yield result_msg

            session.client.receive_response = mock_receive

            with patch.object(session, "_record_message_count"):
                sid, text, meta = await session._query_once("test")

            mock_compact.assert_awaited_once()
            assert session.last_context_tokens == 50000

    async def test_hot_session_still_compacts_when_over_limit(self):
        """即使 session 温度是 hot，超限也要 compact。"""
        session = _LiveSession("ch1", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.last_context_tokens = 300000

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=True) as mock_compact,
            patch.object(session, "_get_session_temperature", return_value="hot"),
            patch.object(session, "_reset_session", new_callable=AsyncMock) as mock_reset,
        ):
            session.client.query = AsyncMock()
            result_msg = _make_result_msg(usage={"input_tokens": 10000})

            async def mock_receive():
                yield result_msg

            session.client.receive_response = mock_receive

            with patch.object(session, "_record_message_count"):
                await session._query_once("test")

            mock_compact.assert_awaited_once()
            mock_reset.assert_not_awaited()

    async def test_no_compact_when_under_limit(self):
        """未超限时不触发强制 compact（温度策略不受影响）。"""
        session = _LiveSession("ch1", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.last_context_tokens = 100000

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock) as mock_compact,
            patch.object(session, "_get_session_temperature", return_value="hot"),
        ):
            session.client.query = AsyncMock()
            result_msg = _make_result_msg(usage={"input_tokens": 100000})

            async def mock_receive():
                yield result_msg

            session.client.receive_response = mock_receive

            with patch.object(session, "_record_message_count"):
                await session._query_once("test")

            mock_compact.assert_not_awaited()

    async def test_compact_failure_does_not_block_query(self):
        """compact 失败时，query 仍应继续执行。"""
        session = _LiveSession("ch1", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.last_context_tokens = 250000

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=False) as mock_compact,
            patch.object(session, "_get_session_temperature", return_value="hot"),
        ):
            session.client.query = AsyncMock()
            result_msg = _make_result_msg(result="still works", usage={"input_tokens": 250000})

            async def mock_receive():
                yield result_msg

            session.client.receive_response = mock_receive

            with patch.object(session, "_record_message_count"):
                sid, text, meta = await session._query_once("test")

            mock_compact.assert_awaited_once()
            assert text == "still works"
            assert session.last_context_tokens == 250000

    async def test_force_compact_resets_tokens_on_success(self):
        """compact 成功后 last_context_tokens 归零（无新 usage 则保持 0）。"""
        session = _LiveSession("ch1", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.last_context_tokens = 250000

        with (
            patch.object(session, "_ensure_connected", new_callable=AsyncMock),
            patch.object(session, "_compact_session", new_callable=AsyncMock, return_value=True),
            patch.object(session, "_get_session_temperature", return_value="hot"),
        ):
            session.client.query = AsyncMock()
            result_msg = _make_result_msg(usage=None)

            async def mock_receive():
                yield result_msg

            session.client.receive_response = mock_receive

            with (
                patch.object(session, "_record_message_count"),
                patch.object(session, "_estimate_tokens_from_jsonl", return_value=0),
            ):
                await session._query_once("test")

        assert session.last_context_tokens == 0


class TestContextThresholdsConfig:
    """SESSION_CONTEXT_SOFT/HARD_THRESHOLD 配置项验证。"""

    def test_soft_threshold_default(self):
        from brain.config import SESSION_CONTEXT_SOFT_THRESHOLD
        assert SESSION_CONTEXT_SOFT_THRESHOLD == 160000

    def test_hard_threshold_default(self):
        from brain.config import SESSION_CONTEXT_HARD_THRESHOLD
        assert SESSION_CONTEXT_HARD_THRESHOLD == 200000

    def test_config_imported_in_cc(self):
        """cc.py 应导入 SESSION_CONTEXT_HARD_THRESHOLD 和 SESSION_CONTEXT_SOFT_THRESHOLD。"""
        assert hasattr(cc, "SESSION_CONTEXT_HARD_THRESHOLD") or "SESSION_CONTEXT_HARD_THRESHOLD" in dir(cc)
        assert hasattr(cc, "SESSION_CONTEXT_SOFT_THRESHOLD") or "SESSION_CONTEXT_SOFT_THRESHOLD" in dir(cc)
