"""Setup 单元测试 — 配置转换、MCP 配置写入。"""

import json

from brain.setup import _apply_config, _write_notion_mcp_config, _yaml_val


class TestYamlVal:
    """_yaml_val: YAML 值格式化。"""

    def test_bool_true(self):
        assert _yaml_val(True) == "true"

    def test_bool_false(self):
        assert _yaml_val(False) == "false"

    def test_int(self):
        assert _yaml_val(42) == "42"

    def test_float(self):
        assert _yaml_val(3.14) == "3.14"

    def test_empty_string(self):
        assert _yaml_val("") == '""'

    def test_string(self):
        assert _yaml_val("hello") == '"hello"'

    def test_string_with_quotes(self):
        result = _yaml_val("ntn_abc123")
        assert result == '"ntn_abc123"'


class TestApplyConfig:
    """_apply_config: YAML 行级配置更新（保留注释）。"""

    def test_updates_scalar_value(self):
        """section.key 格式：section 行无缩进，key 行有缩进。"""
        lines = [
            'notion:\n',
            '  token: ""  # comment\n',
        ]
        result = _apply_config(lines, {"notion": {"token": "ntn_test"}})
        assert '"ntn_test"' in result[1]

    def test_preserves_comments(self):
        lines = [
            'scheduler:\n',
            '  idle_interval: 1800      # 空闲时轮询\n',
        ]
        result = _apply_config(lines, {"scheduler": {"idle_interval": 900}})
        assert "900" in result[1]
        assert "# 空闲时轮询" in result[1]

    def test_skips_dict_values(self):
        """dict/list 类型的值不应被写入。"""
        lines = [
            'roles:\n',
            '  planner:\n',
            '    allowed_tools:\n',
        ]
        result = _apply_config(lines, {"roles": {"planner": {"allowed_tools": ["Read"]}}})
        # planner 是 dict，应被跳过
        assert result[1] == '  planner:\n'

    def test_unknown_key_unchanged(self):
        lines = ['foo: bar\n']
        result = _apply_config(lines, {"other_key": "value"})
        assert result[0] == 'foo: bar\n'

    def test_appends_missing_key_to_section(self):
        """Keys in config but not in the YAML file should be appended to the section."""
        lines = [
            "feishu:",
            "  enabled: false",
            "  app_id: \"\"",
            "",
            "session:",
            "  idle_timeout: 600",
        ]
        config = {
            "feishu": {"enabled": True, "platform": "lark", "app_id": "cli_x"},
            "session": {"idle_timeout": 600},
        }
        result = _apply_config(lines, config)
        joined = "\n".join(result)
        assert 'platform: "lark"' in joined
        # platform should appear between feishu section and session section
        platform_idx = next(i for i, l in enumerate(result) if "platform" in l)
        session_idx = next(i for i, l in enumerate(result) if l.strip() == "session:")
        assert platform_idx < session_idx

    def test_no_duplicate_when_key_exists(self):
        """If the key already exists in the file, it should be updated, not duplicated."""
        lines = [
            "feishu:",
            "  enabled: false",
            "  platform: feishu  # comment",
            "  app_id: \"\"",
        ]
        config = {"feishu": {"enabled": True, "platform": "lark", "app_id": "cli_y"}}
        result = _apply_config(lines, config)
        platform_lines = [l for l in result if "platform" in l]
        assert len(platform_lines) == 1
        assert '"lark"' in platform_lines[0]


class TestWriteNotionMcpConfig:
    """_write_notion_mcp_config: 写入 ~/.claude.json。"""

    def test_creates_new_file(self, tmp_path):
        claude_json = tmp_path / ".claude.json"
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.setup.Path.home", return_value=tmp_path
        ):
            _write_notion_mcp_config("ntn_test", '{"Authorization":"Bearer ntn_test"}')

        data = json.loads(claude_json.read_text())
        assert "notion" in data["mcpServers"]
        assert data["mcpServers"]["notion"]["command"] == "npx"

    def test_preserves_existing_servers(self, tmp_path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {"other": {"command": "other-server"}}
        }))
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.setup.Path.home", return_value=tmp_path
        ):
            _write_notion_mcp_config("ntn_test", '{"Authorization":"Bearer ntn_test"}')

        data = json.loads(claude_json.read_text())
        assert "other" in data["mcpServers"]
        assert "notion" in data["mcpServers"]
