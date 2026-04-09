"""Session 生命周期管理 — channel_id → session_id 映射与状态追踪。"""

from __future__ import annotations

import sqlite3
import time

from brain.config import SESSION_IDLE_TIMEOUT, SESSION_MAX_AGE
from brain.infra.logger import log


def init_session_tables(conn: sqlite3.Connection):
    """创建 v2 session 表。"""
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


def get_active_session(conn: sqlite3.Connection, channel_id: str) -> str | None:
    """获取 channel 的活跃 session_id，如果过期则返回 None。"""
    row = conn.execute(
        """SELECT session_id, last_activity, created_at
           FROM v2_sessions
           WHERE channel_id = ? AND status = 'active'
           ORDER BY last_activity DESC LIMIT 1""",
        (channel_id,),
    ).fetchone()

    if row is None:
        return None

    session_id, last_activity, created_at = row["session_id"], row["last_activity"], row["created_at"]
    now = int(time.time())

    # 检查 session 是否过期
    if now - last_activity > SESSION_IDLE_TIMEOUT:
        log.info("[session] channel=%s idle 超时，归档 session=%s", channel_id, session_id)
        _archive_session(conn, channel_id, session_id)
        return None

    if now - created_at > SESSION_MAX_AGE:
        log.info("[session] channel=%s session 过期，归档 session=%s", channel_id, session_id)
        _archive_session(conn, channel_id, session_id)
        return None

    return session_id


def save_session(conn: sqlite3.Connection, channel_id: str, session_id: str):
    """保存或更新 session。"""
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
    """更新 session 的最后活跃时间。"""
    now = int(time.time())
    conn.execute(
        "UPDATE v2_sessions SET last_activity = ? WHERE channel_id = ? AND session_id = ?",
        (now, channel_id, session_id),
    )
    conn.commit()


def _archive_session(conn: sqlite3.Connection, channel_id: str, session_id: str):
    """归档 session。"""
    conn.execute(
        "UPDATE v2_sessions SET status = 'archived' WHERE channel_id = ? AND session_id = ?",
        (channel_id, session_id),
    )
    conn.commit()
