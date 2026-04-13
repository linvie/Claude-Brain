"""executor 模板完整性测试 — 验证 .claude/ 基础设施能完整复制到 workspace。"""

import json

from brain.workspace.setup import _install_role_template


class TestExecutorTemplate:
    """_install_role_template('executor') 必须复制完整的 .claude/ 目录。"""

    def test_settings_json_copied(self, tmp_path):
        _install_role_template(tmp_path, "executor")
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.exists(), "settings.json 应被复制到 .claude/"

    def test_settings_json_has_hooks(self, tmp_path):
        """settings.json 必须包含 hooks 注册（不是只有 permissions）。"""
        _install_role_template(tmp_path, "executor")
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "hooks" in data, "settings.json 应注册 hooks"
        assert "PreToolUse" in data["hooks"], "应有 PreToolUse hook"

    def test_pre_commit_hook_copied_and_executable(self, tmp_path):
        _install_role_template(tmp_path, "executor")
        hook = tmp_path / ".claude" / "hooks" / "pre-commit-detect.sh"
        assert hook.exists(), "pre-commit-detect.sh 应被复制"
        # 可执行位通过 shutil.copy2 保留
        import os
        assert os.access(hook, os.X_OK), "hook 脚本应可执行"

    def test_qa_skill_copied(self, tmp_path):
        _install_role_template(tmp_path, "executor")
        skill = tmp_path / ".claude" / "skills" / "qa" / "SKILL.md"
        assert skill.exists(), "/qa skill 应被复制"
        content = skill.read_text()
        assert "name: qa" in content
        assert "Auto-Detect" in content

    def test_review_skill_copied(self, tmp_path):
        _install_role_template(tmp_path, "executor")
        skill = tmp_path / ".claude" / "skills" / "review" / "SKILL.md"
        assert skill.exists(), "/review skill 应被复制"
        assert "name: review" in skill.read_text()

    def test_test_run_skill_copied(self, tmp_path):
        _install_role_template(tmp_path, "executor")
        skill = tmp_path / ".claude" / "skills" / "test-run" / "SKILL.md"
        assert skill.exists(), "/test-run skill 应被复制"
        assert "name: test-run" in skill.read_text()

    def test_claude_md_copied(self, tmp_path):
        _install_role_template(tmp_path, "executor")
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        # 验证新增的章节存在
        assert "项目类型自适应" in content
        assert "可用 Skills" in content
        assert "/qa" in content


class TestPlannerTemplate:
    """planner 模板的 .claude/ 同步 + CLAUDE.md 增强。"""

    def test_settings_json_copied(self, tmp_path):
        _install_role_template(tmp_path, "planner")
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.exists()

    def test_claude_md_has_acceptance_criteria_format(self, tmp_path):
        _install_role_template(tmp_path, "planner")
        content = (tmp_path / "CLAUDE.md").read_text()
        # 验证强化后的关键章节
        assert "任务粒度" in content
        assert "验收标准" in content
        assert "验证方式" in content
        assert "工具调用次数" in content  # 新增的具体粒度规则
