"""CC 执行器单元测试 — session 状态管理、model 切换。

覆盖历史 bug：
- model switch 不生效（set_model 后 session 未断开）
- model 不持久（restart 后 _sessions 空，override 丢失）
- get_session_info 读不到 model（未查 _model_overrides）
- 新 session 未继承 _model_overrides
"""

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
