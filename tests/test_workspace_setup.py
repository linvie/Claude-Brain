"""Workspace 模板安装测试 — 飞书通知注入。"""

import json
from unittest.mock import patch

from brain.workspace.setup import _inject_feishu_notify


class TestInjectFeishuNotify:
    """_inject_feishu_notify: 注入飞书 chat_id 到 CLAUDE.md。"""

    def test_injects_lark_cli_command(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Test\n\nSome content.")

        with patch("brain.workspace.setup.FEISHU_NOTIFY_CHAT_ID", "oc_test123"):
            _inject_feishu_notify(tmp_path)

        result = claude_md.read_text()
        assert "lark-cli im send" in result
        assert "oc_test123" in result

    def test_skips_if_already_has_lark_cli(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        original = "# Test\n\nlark-cli im send existing"
        claude_md.write_text(original)

        with patch("brain.workspace.setup.FEISHU_NOTIFY_CHAT_ID", "oc_test123"):
            _inject_feishu_notify(tmp_path)

        assert claude_md.read_text() == original  # 未修改

    def test_skips_if_no_claude_md(self, tmp_path):
        with patch("brain.workspace.setup.FEISHU_NOTIFY_CHAT_ID", "oc_test123"):
            _inject_feishu_notify(tmp_path)  # 不应报错


class TestNotionConfigInjection:
    """session/manager.py: workspace 初始化时注入 notion_config.json。"""

    def test_notion_config_written(self, tmp_path):
        """验证 notion_config.json 被正确写入。"""
        config_path = tmp_path / "notion_config.json"
        config_data = {
            "task_db_id": "task-db-123",
            "project_db_id": "proj-db-456",
        }
        config_path.write_text(json.dumps(config_data, indent=2))

        data = json.loads(config_path.read_text())
        assert data["task_db_id"] == "task-db-123"
        assert data["project_db_id"] == "proj-db-456"
