"""Raw Ledger — session 关闭时归档 SDK JSONL 对话记录。

Phase B 基础设施：将 Claude SDK 的原始对话 JSONL 复制到
~/.ccbrain/memory/ledger/{session_id}.jsonl，供后续 LLM 提取和 Daily Views 使用。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from brain.config import MEMORY_LEDGER_DIR
from brain.infra.logger import log_memory as log


def get_ledger_dir() -> Path:
    """返回 ledger 目录路径，不存在则创建。"""
    MEMORY_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_LEDGER_DIR


def archive_session_jsonl(session_id: str, sdk_jsonl_path: Path) -> Path | None:
    """复制 SDK JSONL 到 ledger 目录，返回归档路径。

    源文件不存在或为空时返回 None。
    """
    if not sdk_jsonl_path.exists():
        log.warning("[ledger] SDK JSONL 不存在: %s", sdk_jsonl_path)
        return None

    if sdk_jsonl_path.stat().st_size == 0:
        log.debug("[ledger] SDK JSONL 为空，跳过: %s", sdk_jsonl_path)
        return None

    dest = get_ledger_dir() / f"{session_id}.jsonl"
    shutil.copy2(sdk_jsonl_path, dest)
    log.info("[ledger] 归档 session %s → %s (%d bytes)",
             session_id, dest, dest.stat().st_size)
    return dest


def get_session_jsonl(session_id: str) -> Path | None:
    """根据 session_id 获取已归档的 JSONL 路径，不存在返回 None。"""
    path = get_ledger_dir() / f"{session_id}.jsonl"
    return path if path.exists() else None
