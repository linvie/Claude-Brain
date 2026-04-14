"""配置加载 — 读取 ~/.ccbrain/config.yaml，导出 CONFIG 和派生常量。"""

from pathlib import Path

import yaml

# brain 包目录
PKG_DIR = Path(__file__).resolve().parent

# 源码目录（brain/ 包的父目录，editable 模式下有用）
SRC_DIR = PKG_DIR.parent

# 打包资源目录（brain/data/）
RESOURCE_DIR = PKG_DIR / "data"

# 运行时数据目录
DATA_DIR = Path.home() / ".ccbrain"

# 配置文件路径
CONFIG_PATH = DATA_DIR / "config.yaml"
CONFIG_EXAMPLE_PATH = RESOURCE_DIR / "config.example.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()

# 调度
IDLE_INTERVAL = CONFIG.get("scheduler", {}).get("idle_interval", 1800)
ACTIVE_INTERVAL = CONFIG.get("scheduler", {}).get("active_interval", 30)
COOLDOWN_INTERVAL = CONFIG.get("scheduler", {}).get("cooldown_interval", 120)
COOLDOWN_DURATION = CONFIG.get("scheduler", {}).get("cooldown_duration", 900)
MAX_CONCURRENT = CONFIG.get("scheduler", {}).get("max_concurrent", 3)
MAX_TASK_DURATION = CONFIG.get("task", {}).get("max_duration", 7200)

# 固定路径（全部在 ~/.ccbrain/ 下）
WORKSPACE_BASE = DATA_DIR / "workspaces"
DB_PATH = DATA_DIR / "state.db"
LOG_DIR = DATA_DIR / "logs"

# 远程开发模式
REMOTE_ENABLED = CONFIG.get("remote", {}).get("enabled", False)
REMOTE_HOST = CONFIG.get("remote", {}).get("host", "localhost")
REMOTE_SHARE_DIR = Path(CONFIG.get("remote", {}).get("share_dir", "~/brain-shared")).expanduser()

# Notion 启用判断（token 非空即启用）
NOTION_ENABLED = bool(CONFIG.get("notion", {}).get("token", ""))

# v2: 飞书配置
_feishu_cfg = CONFIG.get("feishu", {})
FEISHU_ENABLED = _feishu_cfg.get("enabled", False)
FEISHU_APP_ID = _feishu_cfg.get("app_id", "")
FEISHU_APP_SECRET = _feishu_cfg.get("app_secret", "")
FEISHU_ALLOWED_USERS = _feishu_cfg.get("allowed_users", [])  # 空列表 = 不限制
FEISHU_NOTIFY_CHAT_ID = _feishu_cfg.get("notify_chat_id", "")  # v1 任务通知 chat_id

# Notion DB ID（供 v2 飞书对话注入）
_notion_cfg = CONFIG.get("notion", {})
NOTION_TASK_DB_ID = _notion_cfg.get("task_db_id", "")
NOTION_PROJECT_DB_ID = _notion_cfg.get("project_db_id", "")

# v2: Session 配置
_session_cfg = CONFIG.get("session", {})
SESSION_IDLE_TIMEOUT = _session_cfg.get("idle_timeout", 600)
SESSION_MAX_AGE = _session_cfg.get("max_age", 604800)

# v2: 记忆配置
_memory_cfg = CONFIG.get("memory", {})
MEMORY_DECAY_HALF_LIFE = _memory_cfg.get("decay_half_life_days", 30)
MEMORY_ALWAYS_ON_THRESHOLD = _memory_cfg.get("always_on_threshold", 8)
MEMORY_MAX_CONTEXT_TOKENS = _memory_cfg.get("max_context_tokens", 2000)
