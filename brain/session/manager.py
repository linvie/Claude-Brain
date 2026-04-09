"""Session 生命周期管理 — per-channel CC 持久会话 + workspace 自动创建。"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from pathlib import Path

from brain.config import DATA_DIR, SESSION_IDLE_TIMEOUT, SRC_DIR, WORKSPACE_BASE
from brain.infra.logger import log_session as log

# CLAUDE.md 模板路径（源码目录下）
_TEMPLATE_DIR = SRC_DIR / "templates" / "v2"


def init_session_tables(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS v2_sessions (
            channel_id   TEXT NOT NULL,
            session_id   TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'active',
            created_at   INTEGER NOT NULL,
            last_activity INTEGER NOT NULL,
            PRIMARY KEY (channel_id, session_id)
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Workspace 管理
# ---------------------------------------------------------------------------

def get_workspace(channel_id: str) -> Path:
    """获取 channel 的 workspace 路径，不存在则自动创建并初始化。"""
    workspace = WORKSPACE_BASE / channel_id
    if not workspace.exists():
        _init_workspace(workspace, channel_id)
    return workspace


def _init_workspace(workspace: Path, channel_id: str):
    """初始化 workspace：创建目录 + 复制模板。"""
    workspace.mkdir(parents=True, exist_ok=True)
    log.info("创建 workspace: %s", workspace)

    # 复制 v2 模板（如果存在）
    if _TEMPLATE_DIR.exists():
        for src in _TEMPLATE_DIR.iterdir():
            if src.is_file():
                shutil.copy2(src, workspace / src.name)
                log.debug("复制模板: %s", src.name)
            elif src.is_dir():
                dest = workspace / src.name
                if not dest.exists():
                    shutil.copytree(src, dest)
    else:
        # 无模板时创建基础 CLAUDE.md
        claude_md = workspace / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                "# CCBrain Workspace\n\n"
                f"Channel: {channel_id}\n\n"
                "这是 CCBrain 的工作目录。你可以在这里自由工作。\n"
            )


# ---------------------------------------------------------------------------
# Session 数据库操作
# ---------------------------------------------------------------------------

def get_active_session(conn: sqlite3.Connection, channel_id: str) -> str | None:
    row = conn.execute(
        """SELECT session_id, last_activity
           FROM v2_sessions
           WHERE channel_id = ? AND status = 'active'
           ORDER BY last_activity DESC LIMIT 1""",
        (channel_id,),
    ).fetchone()

    if row is None:
        return None

    session_id, last_activity = row["session_id"], row["last_activity"]
    now = int(time.time())

    if now - last_activity > SESSION_IDLE_TIMEOUT:
        log.info("session idle 超时: channel=%s, session=%s", channel_id, session_id)
        _archive_session(conn, channel_id, session_id)
        return None

    return session_id


def save_session(conn: sqlite3.Connection, channel_id: str, session_id: str):
    now = int(time.time())
    conn.execute(
        """INSERT INTO v2_sessions (channel_id, session_id, status, created_at, last_activity)
           VALUES (?, ?, 'active', ?, ?)
           ON CONFLICT (channel_id, session_id) DO UPDATE SET
               last_activity = excluded.last_activity,
               status = 'active'""",
        (channel_id, session_id, now, now),
    )
    conn.commit()


def touch_session(conn: sqlite3.Connection, channel_id: str, session_id: str):
    now = int(time.time())
    conn.execute(
        "UPDATE v2_sessions SET last_activity = ? WHERE channel_id = ? AND session_id = ?",
        (now, channel_id, session_id),
    )
    conn.commit()


def _archive_session(conn: sqlite3.Connection, channel_id: str, session_id: str):
    conn.execute(
        "UPDATE v2_sessions SET status = 'archived' WHERE channel_id = ? AND session_id = ?",
        (channel_id, session_id),
    )
    conn.commit()
