"""日志初始化 — 配置分类 logger 并导出。

所有 logger 均为 'brain' 的子 logger，通过 propagation 自动继承
主 logger 的 console handler，因此所有级别 >= INFO 的日志都会在终端显示。

每个分类 logger 额外绑定独立的文件 handler，方便按模块查看。
"""

import logging
from pathlib import Path

from brain.config import BASE_DIR, CONFIG

log_cfg = CONFIG["logging"]
log_dir = Path(log_cfg["dir"])
if not log_dir.is_absolute():
    log_dir = BASE_DIR / log_dir
log_dir.mkdir(parents=True, exist_ok=True)

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_formatter = logging.Formatter(LOG_FORMAT)
_file_level = getattr(logging, log_cfg["level"])


def _make_file_handler(filename: str) -> logging.FileHandler:
    handler = logging.FileHandler(log_dir / filename)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_formatter)
    return handler


# ── 主 logger（全量日志 + 终端输出） ──
log = logging.getLogger("brain")
log.setLevel(logging.DEBUG)

_main_handler = _make_file_handler("brain.log")
_main_handler.setLevel(_file_level)
log.addHandler(_main_handler)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_formatter)
log.addHandler(_console_handler)

# ── v1 分类 logger ──
log_scheduler = logging.getLogger("brain.scheduler")
log_scheduler.addHandler(_make_file_handler("scheduler.log"))

log_cc = logging.getLogger("brain.cc")
log_cc.addHandler(_make_file_handler("cc.log"))

log_notion = logging.getLogger("brain.notion")
log_notion.addHandler(_make_file_handler("notion.log"))

# ── v2 分类 logger ──
log_feishu = logging.getLogger("brain.feishu")
log_feishu.addHandler(_make_file_handler("feishu.log"))

log_session = logging.getLogger("brain.session")
log_session.addHandler(_make_file_handler("session.log"))

log_memory = logging.getLogger("brain.memory")
log_memory.addHandler(_make_file_handler("memory.log"))
