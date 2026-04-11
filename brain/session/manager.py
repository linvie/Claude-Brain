"""Session 生命周期管理 — per-channel CC 持久会话 + workspace 自动创建。"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from brain.config import (
    NOTION_ENABLED,
    NOTION_PROJECT_DB_ID,
    NOTION_TASK_DB_ID,
    RESOURCE_DIR,
    SESSION_IDLE_TIMEOUT,
    WORKSPACE_BASE,
)
from brain.infra.logger import log_session as log

# v2 workspace 模板（brain/data/template/）
_TEMPLATE_DIR = RESOURCE_DIR / "template"


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
        # 复制 CLAUDE.md
        template_claude = _TEMPLATE_DIR / "CLAUDE.md"
        if template_claude.exists():
            shutil.copy2(template_claude, workspace / "CLAUDE.md")
            _inject_channel_id(workspace, channel_id)
        # 同步其他文件和目录（.claude/ 等）
        _sync_template_extras(workspace)
        # 注入 Notion 配置
        if NOTION_ENABLED and NOTION_TASK_DB_ID:
            import json
            (workspace / "notion_config.json").write_text(
                json.dumps({"task_db_id": NOTION_TASK_DB_ID, "project_db_id": NOTION_PROJECT_DB_ID}, indent=2)
            )
    else:
        # 无模板时创建基础 CLAUDE.md
        claude_md = workspace / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                "# CCBrain Workspace\n\n"
                f"Channel: {channel_id}\n\n"
                "这是 CCBrain 的工作目录。你可以在这里自由工作。\n"
            )


_TEMPLATE_START = "<!-- CCBRAIN_TEMPLATE_START -->"
_TEMPLATE_END = "<!-- CCBRAIN_TEMPLATE_END -->"


def update_workspace_template(workspace: Path, channel_id: str):
    """更新 workspace 的 CLAUDE.md 模板区域，保留用户/CC 自定义内容。

    CLAUDE.md 用标记分区：
    - <!-- CCBRAIN_TEMPLATE_START --> ... <!-- CCBRAIN_TEMPLATE_END --> 之间是模板
    - 标记之后的内容由 CC 或用户维护，不会被覆盖
    """
    claude_md = workspace / "CLAUDE.md"
    template_file = _TEMPLATE_DIR / "CLAUDE.md"

    if not template_file.exists():
        return

    new_template = template_file.read_text()

    if claude_md.exists():
        existing = claude_md.read_text()
        if _TEMPLATE_START in existing and _TEMPLATE_END in existing:
            # 替换标记之间的内容，保留标记之后的用户内容
            before_start = existing.split(_TEMPLATE_START)[0]
            after_end = existing.split(_TEMPLATE_END)[1]
            # 从新模板提取标记区域
            new_section = new_template[
                new_template.index(_TEMPLATE_START):new_template.index(_TEMPLATE_END) + len(_TEMPLATE_END)
            ]
            result = before_start + new_section + after_end
        else:
            # 旧格式（无标记），全部替换为新模板
            result = new_template
    else:
        result = new_template

    # 注入 channel_id
    result = result.replace("CHAT_ID", channel_id)
    claude_md.write_text(result)

    # 同步模板中的其他文件和目录（.claude/ 等）
    _sync_template_extras(workspace)

    # 同步 notion_config.json
    if NOTION_ENABLED and NOTION_TASK_DB_ID:
        import json
        (workspace / "notion_config.json").write_text(
            json.dumps({"task_db_id": NOTION_TASK_DB_ID, "project_db_id": NOTION_PROJECT_DB_ID}, indent=2)
        )

    log.debug("更新模板: %s", workspace.name)


def _sync_template_extras(workspace: Path):
    """同步模板目录中除 CLAUDE.md 外的所有文件和子目录到 workspace。"""
    if not _TEMPLATE_DIR.exists():
        return
    for src in _TEMPLATE_DIR.iterdir():
        if src.name == "CLAUDE.md":
            continue  # CLAUDE.md 由 update_workspace_template 单独处理
        dest = workspace / src.name
        if src.is_file():
            shutil.copy2(src, dest)
        elif src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            for child in src.rglob("*"):
                if child.is_file():
                    rel = child.relative_to(src)
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(child, target)


def _inject_channel_id(workspace: Path, channel_id: str):
    """在 CLAUDE.md 中替换 CHAT_ID 占位符。"""
    claude_md = workspace / "CLAUDE.md"
    if not claude_md.exists():
        return
    content = claude_md.read_text()
    if "CHAT_ID" not in content:
        return
    content = content.replace("CHAT_ID", channel_id)
    claude_md.write_text(content)
    log.debug("注入 channel_id: %s → CLAUDE.md", channel_id)


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
