"""CLI 单元测试 — plist 生成、版本号提取。"""

from unittest.mock import patch

from brain.cli import _generate_plist, _version


class TestGeneratePlist:
    """_generate_plist: launchd plist 生成。"""

    def test_contains_path_env(self):
        """plist 必须包含 EnvironmentVariables PATH。"""
        with patch("brain.cli._uv", return_value="/usr/local/bin/uv"):
            plist = _generate_plist()
        assert "<key>PATH</key>" in plist
        assert "<key>EnvironmentVariables</key>" in plist

    def test_inherits_shell_path(self):
        """PATH 应从当前 shell 环境继承。"""
        test_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin"
        with (
            patch("brain.cli._uv", return_value="/usr/local/bin/uv"),
            patch.dict("os.environ", {"PATH": test_path}),
        ):
            plist = _generate_plist()
        assert test_path in plist

    def test_contains_label(self):
        with patch("brain.cli._uv", return_value="/usr/local/bin/uv"):
            plist = _generate_plist()
        assert "com.linvie.ccbrain" in plist

    def test_contains_keepalive(self):
        with patch("brain.cli._uv", return_value="/usr/local/bin/uv"):
            plist = _generate_plist()
        assert "<key>KeepAlive</key>" in plist


class TestVersion:
    """_version: 版本号提取。"""

    def test_returns_string(self):
        v = _version()
        assert isinstance(v, str)
        assert v != ""

    def test_matches_pyproject(self):
        """版本号应与 pyproject.toml 一致。"""
        import re
        from pathlib import Path

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        m = re.search(r'version\s*=\s*"([^"]+)"', pyproject.read_text())
        assert m
        assert _version() == m.group(1)
