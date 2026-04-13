"""CC 执行器单元测试 — session 状态管理、model 切换。

覆盖历史 bug：
- model switch 不生效（set_model 后 session 未断开）
- model 不持久（restart 后 _sessions 空，override 丢失）
- get_session_info 读不到 model（未查 _model_overrides）
- 新 session 未继承 _model_overrides
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# 直接操作模块级状态，不启动真实 CC 进程
from brain.executor import cc


@pytest.fixture(autouse=True)
def clean_state():
    """每个测试前清理全局状态。"""
    cc._sessions.clear()
    cc._model_overrides.clear()
    yield
    cc._sessions.clear()
    cc._model_overrides.clear()


class TestModelOverrides:
    """_model_overrides: 跨 session 生命周期持久化 model 选择。"""

    async def test_set_model_without_session(self):
        """session 不存在时，set_model 应存入 _model_overrides。"""
        result = await cc.set_model("ch1", "sonnet")
        assert result == "sonnet"
        assert cc._model_overrides["ch1"] == "sonnet"

    async def test_set_model_default(self):
        """model=None 应返回 'default'。"""
        result = await cc.set_model("ch1", None)
        assert result == "default"
        assert cc._model_overrides["ch1"] is None

    async def test_set_model_with_session_disconnects(self):
        """session 存在且已连接时，set_model 应断开连接。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        session._connected = True
        session.client = AsyncMock()
        session.client.disconnect = AsyncMock()
        cc._sessions["ch1"] = session

        await cc.set_model("ch1", "haiku")

        assert session.model == "haiku"
        assert cc._model_overrides["ch1"] == "haiku"
        session.client.disconnect.assert_awaited_once()

    async def test_set_model_with_disconnected_session(self):
        """session 存在但未连接时，不应尝试断开。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        session._connected = False
        session.client = None
        cc._sessions["ch1"] = session

        await cc.set_model("ch1", "opus")

        assert session.model == "opus"
        assert cc._model_overrides["ch1"] == "opus"


class TestGetSessionInfo:
    """get_session_info: 读取 channel 的会话状态。"""

    def test_no_session_no_override(self):
        """无 session 无 override → model 为 default。"""
        info = cc.get_session_info("ch1")
        assert info["connected"] is False
        assert info["model"] == "default"
        assert info["total_cost"] == 0

    def test_no_session_with_override(self):
        """无 session 但有 override → 应读到 override 的 model。"""
        cc._model_overrides["ch1"] = "sonnet"
        info = cc.get_session_info("ch1")
        assert info["model"] == "sonnet"
        assert info["connected"] is False

    def test_session_model_takes_priority(self):
        """session 有 model 时优先于 override。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        session.model = "opus"
        session._connected = True
        session.total_cost = 0.1234
        session.total_queries = 5
        cc._sessions["ch1"] = session
        cc._model_overrides["ch1"] = "sonnet"

        info = cc.get_session_info("ch1")
        assert info["model"] == "opus"  # session 优先
        assert info["connected"] is True
        assert info["total_cost"] == 0.1234
        assert info["total_queries"] == 5

    def test_session_no_model_falls_to_override(self):
        """session.model 为 None 时，应 fallback 到 override。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        session.model = None
        cc._sessions["ch1"] = session
        cc._model_overrides["ch1"] = "haiku"

        info = cc.get_session_info("ch1")
        assert info["model"] == "haiku"


class TestExecuteSessionCreation:
    """execute: 新 session 应继承 _model_overrides。"""

    async def test_new_session_inherits_override(self):
        """预设 model override 后创建 session，应自动应用。"""
        cc._model_overrides["ch1"] = "sonnet"

        # mock _LiveSession.query 避免实际连接
        with patch.object(cc._LiveSession, "query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = ("sid-1", "result")
            await cc.execute(
                prompt="test",
                cwd="/tmp",
                channel_id="ch1",
            )

        session = cc._sessions["ch1"]
        assert session.model == "sonnet"

    async def test_reuse_session_same_cwd(self):
        """相同 cwd 应复用 session。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        cc._sessions["ch1"] = session

        with patch.object(cc._LiveSession, "query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = ("sid-1", "result")
            await cc.execute(
                prompt="test",
                cwd="/tmp",
                channel_id="ch1",
                system_append="new-append",
            )

        # 应复用原 session，但更新 system_append
        assert cc._sessions["ch1"] is session
        assert session._system_append == "new-append"

    async def test_new_session_on_cwd_change(self):
        """cwd 变化应创建新 session。"""
        old_session = cc._LiveSession("ch1", "/old", "")
        cc._sessions["ch1"] = old_session

        with patch.object(cc._LiveSession, "query", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = ("sid-1", "result")
            await cc.execute(
                prompt="test",
                cwd="/new",
                channel_id="ch1",
            )

        assert cc._sessions["ch1"] is not old_session


class TestBuildOptions:
    """_build_options: 确保 SDK 参数正确构建。"""

    def test_always_uses_claude_code_preset(self):
        """必须始终使用 claude_code preset，否则 SDK 跳过 CLAUDE.md。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        opts = session._build_options()
        assert opts.system_prompt["type"] == "preset"
        assert opts.system_prompt["preset"] == "claude_code"

    def test_preset_with_append(self):
        """有 system_append 时，preset 中应包含 append 字段。"""
        session = cc._LiveSession("ch1", "/tmp", "额外指令")
        opts = session._build_options()
        assert opts.system_prompt["append"] == "额外指令"

    def test_preset_without_append(self):
        """无 system_append 时，preset 中不应有 append 字段。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        opts = session._build_options()
        assert "append" not in opts.system_prompt

    def test_setting_sources(self):
        """setting_sources 必须包含 project + user + local。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        opts = session._build_options()
        assert opts.setting_sources == ["project", "user", "local"]

    def test_model_passed_to_options(self):
        """session.model 应传入 ClaudeAgentOptions。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        session.model = "sonnet"
        opts = session._build_options()
        assert opts.model == "sonnet"

    def test_resume_passed_to_options(self):
        session = cc._LiveSession("ch1", "/tmp", "")
        opts = session._build_options(resume="sid-123")
        assert opts.resume == "sid-123"

    def test_bypass_permissions(self):
        session = cc._LiveSession("ch1", "/tmp", "")
        opts = session._build_options()
        assert opts.permission_mode == "bypassPermissions"


class TestQueryErrorHandling:
    """query(): 三层错误处理 — 自愈、超时、友好提示。"""

    async def test_process_transport_auto_recovery(self):
        """ProcessTransport 错误应自动重连重试并返回成功结果。"""
        session = cc._LiveSession("ch1", "/tmp", "")
        session.session_id = "prev-session"

        # 第一次 _query_once 抛 ProcessTransport，第二次成功
        call_count = [0]

        async def mock_query_once(prompt, resume=None, on_stream=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("ProcessTransport is not ready for writing")
            return ("new-session", "recovered result")

        with patch.object(session, "_query_once", side_effect=mock_query_once):
            result = await session.query("test")

        assert call_count[0] == 2  # 重试了一次
        assert result == ("new-session", "recovered result")
        assert session._connected is False  # 被清理
        assert session.client is None

    async def test_process_transport_recovery_fails(self):
        """自愈重试仍失败应返回友好错误文案。"""
        session = cc._LiveSession("ch1", "/tmp", "")

        async def mock_query_once(prompt, resume=None, on_stream=None):
            raise Exception("ProcessTransport is not ready for writing")

        with patch.object(session, "_query_once", side_effect=mock_query_once):
            session_id, result = await session.query("test")

        assert session_id is None
        assert "会话临时中断" in result
        assert "/reset" in result

    async def test_timeout_error_returns_friendly_message(self):
        """asyncio.TimeoutError 应断开重连并返回超时提示。"""
        session = cc._LiveSession("ch1", "/tmp", "")

        async def mock_query_once(prompt, resume=None, on_stream=None):
            raise asyncio.TimeoutError()

        with (
            patch.object(session, "_query_once", side_effect=mock_query_once),
            patch.object(session, "_disconnect", new_callable=AsyncMock) as mock_disc,
        ):
            session_id, result = await session.query("test")

        assert session_id is None
        assert "超时" in result
        mock_disc.assert_awaited_once()

    async def test_generic_error_returns_friendly_message(self):
        """其他异常应返回通用错误提示，不抛出给上层。"""
        session = cc._LiveSession("ch1", "/tmp", "")

        async def mock_query_once(prompt, resume=None, on_stream=None):
            raise ValueError("random error")

        with patch.object(session, "_query_once", side_effect=mock_query_once):
            session_id, result = await session.query("test")

        assert session_id is None
        assert "ValueError" in result
        assert "/reset" in result

    async def test_normal_query_returns_result(self):
        """正常情况下直接返回 _query_once 的结果。"""
        session = cc._LiveSession("ch1", "/tmp", "")

        async def mock_query_once(prompt, resume=None, on_stream=None):
            return ("sid-ok", "ok result")

        with patch.object(session, "_query_once", side_effect=mock_query_once):
            result = await session.query("test")

        assert result == ("sid-ok", "ok result")


class TestOneShotQuery:
    """one_shot_query: 独立 CC 进程，零状态污染。"""

    async def test_returns_result_text(self):
        """正常情况下返回 result_text 字符串。"""
        async def mock_consume(prompt, options):
            return "diagnosis result"

        with patch.object(cc, "_consume_one_shot", side_effect=mock_consume):
            result = await cc.one_shot_query("test prompt", "/tmp")
        assert result == "diagnosis result"

    async def test_does_not_pollute_sessions(self):
        """one_shot_query 不应创建或修改 _sessions。"""
        async def mock_consume(prompt, options):
            return "result"

        cc._sessions["existing-channel"] = cc._LiveSession("existing-channel", "/tmp", "")
        sessions_snapshot = dict(cc._sessions)

        with patch.object(cc, "_consume_one_shot", side_effect=mock_consume):
            await cc.one_shot_query("test", "/tmp")

        assert cc._sessions == sessions_snapshot

    async def test_timeout_returns_friendly_message(self):
        """超时应返回友好提示，不抛异常。"""
        async def slow_consume(prompt, options):
            await asyncio.sleep(10)
            return "should not reach"

        with patch.object(cc, "_consume_one_shot", side_effect=slow_consume):
            result = await cc.one_shot_query("test", "/tmp", timeout=0.1)

        assert "超时" in result

    async def test_exception_returns_friendly_message(self):
        """sdk_query 异常应返回友好提示。"""
        async def fail_consume(prompt, options):
            raise RuntimeError("boom")

        with patch.object(cc, "_consume_one_shot", side_effect=fail_consume):
            result = await cc.one_shot_query("test", "/tmp")

        assert "RuntimeError" in result
        assert "boom" in result

    async def test_system_append_passed_to_options(self):
        """system_append 应通过 options.system_prompt.append 传入。"""
        captured_options = {}

        async def capture_consume(prompt, options):
            captured_options["opts"] = options
            return ""

        with patch.object(cc, "_consume_one_shot", side_effect=capture_consume):
            await cc.one_shot_query("test", "/tmp", system_append="custom role")

        assert captured_options["opts"].system_prompt["append"] == "custom role"

    async def test_uses_claude_code_preset(self):
        """必须使用 claude_code preset，否则 SDK 跳过 CLAUDE.md。"""
        captured_options = {}

        async def capture_consume(prompt, options):
            captured_options["opts"] = options
            return ""

        with patch.object(cc, "_consume_one_shot", side_effect=capture_consume):
            await cc.one_shot_query("test", "/tmp")

        assert captured_options["opts"].system_prompt["preset"] == "claude_code"
