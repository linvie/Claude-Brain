"""配置模块单元测试 — 路径常量、配置加载、默认值。

覆盖历史 bug：
- BASE_DIR 改名 SRC_DIR 后漏改引用
- RESOURCE_DIR 必须在 brain/data/ 下（打包到 wheel 中）
"""

from pathlib import Path

from brain.config import (
    CONFIG_EXAMPLE_PATH,
    CONFIG_PATH,
    DATA_DIR,
    DB_PATH,
    LOG_DIR,
    PKG_DIR,
    RESOURCE_DIR,
    SRC_DIR,
    WORKSPACE_BASE,
)


class TestPathConstants:
    """路径常量：确保目录关系正确。"""

    def test_pkg_dir_is_brain(self):
        """PKG_DIR 应指向 brain/ 包目录。"""
        assert PKG_DIR.name == "brain"
        assert PKG_DIR.is_dir()

    def test_src_dir_is_parent_of_pkg(self):
        """SRC_DIR 应是 brain/ 的父目录。"""
        assert SRC_DIR == PKG_DIR.parent

    def test_resource_dir_inside_package(self):
        """RESOURCE_DIR 必须在 brain/ 内（否则 wheel 打不进去）。"""
        assert str(RESOURCE_DIR).startswith(str(PKG_DIR))
        assert RESOURCE_DIR.name == "data"

    def test_resource_dir_exists(self):
        assert RESOURCE_DIR.is_dir()

    def test_config_example_exists(self):
        """config.example.yaml 必须存在于 RESOURCE_DIR。"""
        assert CONFIG_EXAMPLE_PATH.exists()
        assert CONFIG_EXAMPLE_PATH.name == "config.example.yaml"

    def test_data_dir_is_home_ccbrain(self):
        """DATA_DIR 应为 ~/.ccbrain/。"""
        assert DATA_DIR == Path.home() / ".ccbrain"

    def test_derived_paths_under_data_dir(self):
        """所有运行时路径必须在 DATA_DIR 下。"""
        assert str(WORKSPACE_BASE).startswith(str(DATA_DIR))
        assert str(DB_PATH).startswith(str(DATA_DIR))
        assert str(LOG_DIR).startswith(str(DATA_DIR))

    def test_config_path_under_data_dir(self):
        assert str(CONFIG_PATH).startswith(str(DATA_DIR))
        assert CONFIG_PATH.name == "config.yaml"


class TestConfigDefaults:
    """CONFIG 默认值：config.yaml 不存在或缺少字段时的行为。"""

    def test_import_succeeds(self):
        """config 模块应始终可以 import，即使 config.yaml 不存在。"""
        from brain import config
        assert hasattr(config, "CONFIG")

    def test_defaults_are_reasonable(self):
        from brain.config import (
            ACTIVE_INTERVAL,
            IDLE_INTERVAL,
            MAX_CONCURRENT,
            MAX_TASK_DURATION,
            SESSION_IDLE_TIMEOUT,
        )
        # 默认值应为正整数
        assert IDLE_INTERVAL > 0
        assert ACTIVE_INTERVAL > 0
        assert MAX_CONCURRENT > 0
        assert MAX_TASK_DURATION > 0
        assert SESSION_IDLE_TIMEOUT > 0

    def test_memory_config_defaults(self):
        """记忆系统配置常量应有合理默认值。"""
        from brain.config import (
            MEMORY_ENABLED,
            MEMORY_LEDGER_DIR,
            MEMORY_VIEWS_DIR,
        )
        assert MEMORY_ENABLED is True
        assert MEMORY_LEDGER_DIR.name == "ledger"
        assert "memory" in str(MEMORY_LEDGER_DIR)
        assert MEMORY_VIEWS_DIR.name == "views"
        assert "memory" in str(MEMORY_VIEWS_DIR)
