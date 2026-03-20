"""日志初始化 — 配置 4 个分类 logger 并导出。"""

import logging
from pathlib import Path

from brain.config import BASE_DIR, CONFIG

log_cfg = CONFIG["logging"]
log_dir = Path(log_cfg["dir"])
if not log_dir.is_absolute():
    log_dir = BASE_DIR / log_dir
log_dir.mkdir(parents=True, exist_ok=True)

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"

# 主日志 — 全量记录
log = logging.getLogger("brain")
log.setLevel(logging.DEBUG)

_main_handler = logging.FileHandler(log_dir / "brain.log")
_main_handler.setLevel(getattr(logging, log_cfg["level"]))
_main_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log.addHandler(_main_handler)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log.addHandler(_console_handler)

# 调度日志 — 只记录任务生命周期事件（dispatch / done / blocked / timeout）
log_scheduler = logging.getLogger("brain.scheduler")
_sched_handler = logging.FileHandler(log_dir / "scheduler.log")
_sched_handler.setLevel(logging.DEBUG)
_sched_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log_scheduler.addHandler(_sched_handler)

# CC 进程日志 — 记录 CC 启动、退出、stdout/stderr 摘要
log_cc = logging.getLogger("brain.cc")
_cc_handler = logging.FileHandler(log_dir / "cc.log")
_cc_handler.setLevel(logging.DEBUG)
_cc_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log_cc.addHandler(_cc_handler)

# Notion 交互日志 — 记录所有 Notion API 调用
log_notion = logging.getLogger("brain.notion")
_notion_handler = logging.FileHandler(log_dir / "notion.log")
_notion_handler.setLevel(logging.DEBUG)
_notion_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log_notion.addHandler(_notion_handler)
