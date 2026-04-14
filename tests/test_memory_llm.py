"""brain/memory/_llm.py 单元测试 — CC SDK 封装。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain.memory import _llm


class TestHaikuComplete:
    @pytest.fixture
    def mock_assistant_msg(self):
        """构造模拟的 CC SDK AssistantMessage。"""
        block = MagicMock()
        block.text = "fact|8|用户偏好 Python 开发"
        msg = MagicMock()
        msg.__class__ = type("AssistantMessage", (), {})
        return block, msg

    async def test_success(self):
        """正常调用返回文本。"""
        with patch.object(_llm, "_query_once", new=AsyncMock(return_value="fact|8|用户偏好 Python 开发")):
            result = await _llm.haiku_complete("system", "user msg")
        assert result == "fact|8|用户偏好 Python 开发"

    async def test_returns_empty_on_failure(self):
        """所有重试失败后返回空字符串。"""
        with (
            patch.object(_llm, "_query_once", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(_llm, "_RETRY_DELAY", 0.01),
        ):
            result = await _llm.haiku_complete("system", "user msg")
        assert result == ""

    async def test_retries_on_error(self):
        """失败后重试成功。"""
        mock = AsyncMock(side_effect=[RuntimeError("transient"), "ok result"])
        with (
            patch.object(_llm, "_query_once", new=mock),
            patch.object(_llm, "_RETRY_DELAY", 0.01),
        ):
            result = await _llm.haiku_complete("system", "user msg")
        assert result == "ok result"
        assert mock.await_count == 2

    async def test_retries_exhausted(self):
        """重试耗尽返回空字符串。"""
        mock = AsyncMock(side_effect=RuntimeError("persistent"))
        with (
            patch.object(_llm, "_query_once", new=mock),
            patch.object(_llm, "_RETRY_DELAY", 0.01),
        ):
            result = await _llm.haiku_complete("system", "user msg")
        assert result == ""
        assert mock.await_count == 3  # 1 + 2 retries

    async def test_custom_model(self):
        """支持自定义 model 参数。"""
        mock = AsyncMock(return_value="ok")
        with patch.object(_llm, "_query_once", new=mock):
            await _llm.haiku_complete("system", "msg", model="claude-sonnet-4-6")
        call_kwargs = mock.call_args
        assert call_kwargs[1]["model"] == "claude-sonnet-4-6"

    async def test_default_model(self):
        """默认使用 MEMORY_EXTRACTION_MODEL。"""
        mock = AsyncMock(return_value="ok")
        with patch.object(_llm, "_query_once", new=mock):
            await _llm.haiku_complete("system", "msg")
        call_kwargs = mock.call_args
        assert "haiku" in call_kwargs[1]["model"]


class TestQueryOnce:
    async def test_collects_text_from_assistant_message(self):
        """从 AssistantMessage 的 TextBlock 提取文本。"""
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        text_block = TextBlock(text="extracted text")
        assistant_msg = MagicMock(spec=AssistantMessage)
        assistant_msg.content = [text_block]
        # Make isinstance checks work
        assistant_msg.__class__ = AssistantMessage

        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = None
        result_msg.__class__ = ResultMessage

        async def fake_query(**kwargs):
            for msg in [assistant_msg, result_msg]:
                yield msg

        with patch.object(_llm, "sdk_query", new=fake_query):
            result = await _llm._query_once("system", "user msg", model="test-model")
        assert result == "extracted text"

    async def test_prefers_result_message(self):
        """如果 ResultMessage 有 result，优先使用。"""
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        text_block = TextBlock(text="streaming text")
        assistant_msg = MagicMock(spec=AssistantMessage)
        assistant_msg.content = [text_block]
        assistant_msg.__class__ = AssistantMessage

        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "final result"
        result_msg.__class__ = ResultMessage

        async def fake_query(**kwargs):
            for msg in [assistant_msg, result_msg]:
                yield msg

        with patch.object(_llm, "sdk_query", new=fake_query):
            result = await _llm._query_once("system", "user msg", model="test-model")
        assert result == "final result"
