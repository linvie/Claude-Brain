"""配置加载 — 读取 config.yaml，导出 CONFIG 和派生常量。"""

from pathlib import Path

import yaml

# brain/ 包在项目根的子目录中，所以需要 .parent.parent 才能指向项目根
BASE_DIR = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


CONFIG = load_config()

IDLE_INTERVAL = CONFIG["scheduler"]["idle_interval"]
ACTIVE_INTERVAL = CONFIG["scheduler"]["active_interval"]
COOLDOWN_INTERVAL = CONFIG["scheduler"].get("cooldown_interval", 120)
COOLDOWN_DURATION = CONFIG["scheduler"].get("cooldown_duration", 900)
MAX_CONCURRENT = CONFIG["scheduler"].get("max_concurrent", 3)
MAX_TASK_DURATION = CONFIG["task"]["max_duration"]
WORKSPACE_BASE = Path(CONFIG["workspace"]["base_dir"]).expanduser()
DB_PATH = Path(CONFIG["database"]["path"])
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

# 远程开发模式
REMOTE_ENABLED = CONFIG.get("remote", {}).get("enabled", False)
REMOTE_HOST = CONFIG.get("remote", {}).get("host", "localhost")
REMOTE_SHARE_DIR = Path(CONFIG.get("remote", {}).get("share_dir", "~/brain-shared")).expanduser()
