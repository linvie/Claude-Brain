"""Workspace manager 测试 — clone / copy / init 逻辑。"""

from brain.workspace.manager import _is_remote_url


class TestIsRemoteUrl:
    """_is_remote_url: 区分远程 URL 和本地路径。"""

    def test_https_github(self):
        assert _is_remote_url("https://github.com/user/repo") is True

    def test_git_ssh(self):
        assert _is_remote_url("git@github.com:user/repo.git") is True

    def test_http(self):
        assert _is_remote_url("http://gitlab.com/repo") is True

    def test_local_absolute_path(self):
        assert _is_remote_url("/Users/me/code/project") is False

    def test_local_home_path(self):
        assert _is_remote_url("~/code/project") is False

    def test_local_relative_path(self):
        assert _is_remote_url("./project") is False
