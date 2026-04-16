"""Heartbeat 心跳机制测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from brain.scheduler.heartbeat import (
    build_heartbeat_prompt,
    run_heartbeat,
    run_heartbeat_and_notify,
)

# ---------------------------------------------------------------------------
# build_heartbeat_prompt
# ---------------------------------------------------------------------------


class TestBuildHeartbeatPrompt:
    """build_heartbeat_prompt: 合并系统模板和用户自定义模板。"""

    def test_system_template_included(self, tmp_path):
        """系统模板内容应包含在 prompt 中。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"):
            (tmp_path / "SYS.md").write_text("System checks here")
            result = build_heartbeat_prompt(tmp_path)
            assert "System checks here" in result

    def test_user_template_included(self, tmp_path):
        """用户自定义模板存在时应包含在 prompt 中。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"):
            (tmp_path / "SYS.md").write_text("System")
            (tmp_path / "HEARTBEAT.md").write_text("User custom checks")
            result = build_heartbeat_prompt(tmp_path)
            assert "System" in result
            assert "User custom checks" in result

    def test_user_template_missing(self, tmp_path):
        """用户自定义模板不存在时只返回系统模板。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"):
            (tmp_path / "SYS.md").write_text("System only")
            result = build_heartbeat_prompt(tmp_path)
            assert "System only" in result

    def test_user_template_empty(self, tmp_path):
        """用户自定义模板为空时不包含。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"):
            (tmp_path / "SYS.md").write_text("System")
            (tmp_path / "HEARTBEAT.md").write_text("   ")
            result = build_heartbeat_prompt(tmp_path)
            assert "---" not in result  # 无分隔符 = 无用户内容

    def test_system_template_missing(self, tmp_path):
        """系统模板不存在时返回空（或只有用户模板）。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "nonexistent.md"):
            result = build_heartbeat_prompt(tmp_path)
            assert result == ""

    def test_both_templates_separated_by_divider(self, tmp_path):
        """两个模板之间应有分隔符。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"):
            (tmp_path / "SYS.md").write_text("System part")
            (tmp_path / "HEARTBEAT.md").write_text("User part")
            result = build_heartbeat_prompt(tmp_path)
            assert "---" in result
            assert result.index("System part") < result.index("User part")


# ---------------------------------------------------------------------------
# run_heartbeat
# ---------------------------------------------------------------------------


class TestRunHeartbeat:
    """run_heartbeat: 执行心跳检查并判断是否需要通知。"""

    @pytest.mark.asyncio
    async def test_no_action_returns_none(self, tmp_path):
        """结果包含 NO_ACTION 时返回 None。"""
        with (
            patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"),
            patch("brain.executor.cc.one_shot_query", new_callable=AsyncMock) as mock_query,
        ):
            (tmp_path / "SYS.md").write_text("System")
            mock_query.return_value = "检查完毕，一切正常。NO_ACTION"
            result = await run_heartbeat(tmp_path)
            assert result is None

    @pytest.mark.asyncio
    async def test_action_needed_returns_text(self, tmp_path):
        """结果不含 NO_ACTION 时返回检查文本。"""
        with (
            patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"),
            patch("brain.executor.cc.one_shot_query", new_callable=AsyncMock) as mock_query,
        ):
            (tmp_path / "SYS.md").write_text("System")
            mock_query.return_value = "发现 3 个 ERROR 日志"
            result = await run_heartbeat(tmp_path)
            assert result == "发现 3 个 ERROR 日志"

    @pytest.mark.asyncio
    async def test_empty_result_returns_none(self, tmp_path):
        """空结果返回 None。"""
        with (
            patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"),
            patch("brain.executor.cc.one_shot_query", new_callable=AsyncMock) as mock_query,
        ):
            (tmp_path / "SYS.md").write_text("System")
            mock_query.return_value = ""
            result = await run_heartbeat(tmp_path)
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_none(self, tmp_path):
        """prompt 为空时跳过执行。"""
        with patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "nonexistent.md"):
            result = await run_heartbeat(tmp_path)
            assert result is None

    @pytest.mark.asyncio
    async def test_one_shot_query_called_with_correct_args(self, tmp_path):
        """one_shot_query 应使用正确参数调用。"""
        with (
            patch("brain.scheduler.heartbeat._SYSTEM_TEMPLATE", tmp_path / "SYS.md"),
            patch("brain.executor.cc.one_shot_query", new_callable=AsyncMock) as mock_query,
        ):
            (tmp_path / "SYS.md").write_text("Check stuff")
            mock_query.return_value = "NO_ACTION"
            await run_heartbeat(tmp_path)
            mock_query.assert_called_once()
            call_kwargs = mock_query.call_args
            assert call_kwargs.kwargs["cwd"] == tmp_path
            assert call_kwargs.kwargs["timeout"] == 90.0
            assert "Check stuff" in call_kwargs.kwargs["system_append"]


# ---------------------------------------------------------------------------
# run_heartbeat_and_notify
# ---------------------------------------------------------------------------


class TestRunHeartbeatAndNotify:
    """run_heartbeat_and_notify: 执行心跳并按需通知。"""

    @pytest.mark.asyncio
    async def test_notifies_on_action_needed(self, tmp_path):
        """有异常时调用 notify_feishu。"""
        with (
            patch("brain.scheduler.heartbeat.run_heartbeat", new_callable=AsyncMock) as mock_run,
            patch("brain.infra.feishu_notify.notify_feishu") as mock_notify,
        ):
            mock_run.return_value = "磁盘空间告警"
            await run_heartbeat_and_notify(tmp_path)
            mock_notify.assert_called_once_with("Heartbeat 巡检报告", "磁盘空间告警")

    @pytest.mark.asyncio
    async def test_silent_on_no_action(self, tmp_path):
        """无事发生时不通知。"""
        with (
            patch("brain.scheduler.heartbeat.run_heartbeat", new_callable=AsyncMock) as mock_run,
            patch("brain.infra.feishu_notify.notify_feishu") as mock_notify,
        ):
            mock_run.return_value = None
            await run_heartbeat_and_notify(tmp_path)
            mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self, tmp_path):
        """异常不应传播（静默处理）。"""
        with patch("brain.scheduler.heartbeat.run_heartbeat", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("boom")
            # 不应抛出
            await run_heartbeat_and_notify(tmp_path)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestHeartbeatConfig:
    """心跳配置项正确导出。"""

    def test_config_defaults(self):
        from brain.config import HEARTBEAT_ENABLED, HEARTBEAT_INTERVAL
        # 默认值
        assert isinstance(HEARTBEAT_ENABLED, bool)
        assert isinstance(HEARTBEAT_INTERVAL, int)
        assert HEARTBEAT_INTERVAL > 0


# ---------------------------------------------------------------------------
# _pick_heartbeat_workspace
# ---------------------------------------------------------------------------


class TestPickHeartbeatWorkspace:
    """_pick_heartbeat_workspace: 选择 v2 workspace。"""

    def test_skips_v1_workspace(self, tmp_path):
        from brain.main import _pick_heartbeat_workspace

        with patch("brain.main.WORKSPACE_BASE", tmp_path):
            v1 = tmp_path / "v1ws"
            v1.mkdir()
            (v1 / "inbox.json").write_text("{}")
            result = _pick_heartbeat_workspace()
            assert result is None

    def test_picks_v2_workspace(self, tmp_path):
        from brain.main import _pick_heartbeat_workspace

        with patch("brain.main.WORKSPACE_BASE", tmp_path):
            v2 = tmp_path / "v2ws"
            v2.mkdir()
            result = _pick_heartbeat_workspace()
            assert result == v2

    def test_picks_most_recent(self, tmp_path):
        import time

        from brain.main import _pick_heartbeat_workspace

        with patch("brain.main.WORKSPACE_BASE", tmp_path):
            old = tmp_path / "old"
            old.mkdir()
            time.sleep(0.05)
            new = tmp_path / "new"
            new.mkdir()
            result = _pick_heartbeat_workspace()
            assert result == new

    def test_no_workspace_dir(self, tmp_path):
        from brain.main import _pick_heartbeat_workspace

        with patch("brain.main.WORKSPACE_BASE", tmp_path / "nonexistent"):
            result = _pick_heartbeat_workspace()
            assert result is None


# ---------------------------------------------------------------------------
# Template injection
# ---------------------------------------------------------------------------


class TestTemplateInjection:
    """HEARTBEAT.md 不覆盖用户修改，HEARTBEAT_SYSTEM.md 始终覆盖。"""

    def test_heartbeat_md_not_overwritten(self, tmp_path):
        """用户修改的 HEARTBEAT.md 不应被模板更新覆盖。"""
        from brain.session.manager import _USER_OWNED_FILES, _sync_template_extras

        assert "HEARTBEAT.md" in _USER_OWNED_FILES

        # 模拟 workspace 已有用户自定义 HEARTBEAT.md
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "HEARTBEAT.md").write_text("My custom checks")

        # 模拟模板目录
        tpl = tmp_path / "tpl"
        tpl.mkdir()
        (tpl / "HEARTBEAT.md").write_text("Default template")
        (tpl / "HEARTBEAT_SYSTEM.md").write_text("System v2")

        with patch("brain.session.manager._TEMPLATE_DIR", tpl):
            _sync_template_extras(workspace)

        # HEARTBEAT.md 未被覆盖
        assert (workspace / "HEARTBEAT.md").read_text() == "My custom checks"
        # HEARTBEAT_SYSTEM.md 被覆盖
        assert (workspace / "HEARTBEAT_SYSTEM.md").read_text() == "System v2"

    def test_heartbeat_md_created_when_missing(self, tmp_path):
        """HEARTBEAT.md 不存在时应创建。"""
        from brain.session.manager import _sync_template_extras

        workspace = tmp_path / "ws"
        workspace.mkdir()

        tpl = tmp_path / "tpl"
        tpl.mkdir()
        (tpl / "HEARTBEAT.md").write_text("Default template")

        with patch("brain.session.manager._TEMPLATE_DIR", tpl):
            _sync_template_extras(workspace)

        assert (workspace / "HEARTBEAT.md").read_text() == "Default template"
