"""brain/memory/_llm.py 单元测试 — Haiku API 封装。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain.memory import _llm


@pytest.fixture(autouse=True)
def _reset_client():
    """每个测试重置单例客户端。"""
    _llm._client = None
    yield
    _llm._client = None


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")


class TestGetClient:
    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            _llm._get_client()

    def test_returns_client_with_key(self, mock_env):
        client = _llm._get_client()
        assert client is not None
        # 单例
        assert _llm._get_client() is client


class TestHaikuComplete:
    @pytest.fixture
    def mock_response(self):
        """构造模拟的 Anthropic API 响应。"""
        block = MagicMock()
        block.type = "text"
        block.text = "fact|8|用户偏好 Python 开发"
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 20
        resp = MagicMock()
        resp.content = [block]
        resp.usage = usage
        return resp

    async def test_success(self, mock_env, mock_response):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        _llm._client = mock_client

        result = await _llm.haiku_complete("system", "user msg")
        assert result == "fact|8|用户偏好 Python 开发"
        mock_client.messages.create.assert_awaited_once()

    async def test_returns_empty_on_auth_error(self, mock_env):
        import anthropic
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )
        _llm._client = mock_client

        result = await _llm.haiku_complete("system", "user msg")
        assert result == ""

    async def test_retries_on_rate_limit(self, mock_env, mock_response):
        import anthropic
        mock_client = MagicMock()

        rate_err = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        mock_client.messages.create = AsyncMock(
            side_effect=[rate_err, mock_response]
        )
        _llm._client = mock_client

        with patch("brain.memory._llm._RETRY_DELAY", 0.01):
            result = await _llm.haiku_complete("system", "user msg")

        assert result == "fact|8|用户偏好 Python 开发"
        assert mock_client.messages.create.await_count == 2

    async def test_retries_exhausted_returns_empty(self, mock_env):
        import anthropic
        mock_client = MagicMock()
        rate_err = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body=None,
        )
        mock_client.messages.create = AsyncMock(side_effect=rate_err)
        _llm._client = mock_client

        with patch("brain.memory._llm._RETRY_DELAY", 0.01):
            result = await _llm.haiku_complete("system", "user msg")

        assert result == ""
        assert mock_client.messages.create.await_count == 3  # 1 + 2 retries

    async def test_custom_model(self, mock_env, mock_response):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        _llm._client = mock_client

        await _llm.haiku_complete("system", "msg", model="claude-sonnet-4-6")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"
