"""记忆存储 — SQLite CRUD。"""

from __future__ import annotations

import json
import sqlite3
import time

from brain.infra.logger import log_memory as log


def init_memory_tables(conn: sqlite3.Connection):
    """创建 memories 表。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            type          TEXT NOT NULL,
            content       TEXT NOT NULL,
            source        TEXT,
            tags          TEXT,
            importance    INTEGER DEFAULT 5,
            last_accessed INTEGER,
            created_at    INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def add_memory(
    conn: sqlite3.Connection,
    *,
    type: str,
    content: str,
    source: str | None = None,
    tags: list[str] | None = None,
    importance: int = 5,
) -> int:
    """添加一条记忆，返回 id。"""
    now = int(time.time())
    cursor = conn.execute(
        """INSERT INTO memories (type, content, source, tags, importance, last_accessed, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (type, content, source, json.dumps(tags or []), importance, now, now),
    )
    conn.commit()
    log.debug("[memory] 添加: type=%s, content=%s", type, content[:60])
    return cursor.lastrowid


def search_memories(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    limit: int = 10,
) -> list[dict]:
    """检索记忆：按关键词匹配 content 和 tags，按 importance + recency 排序。"""
    now = int(time.time())

    if query.strip():
        # 关键词匹配
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT id, type, content, tags, importance, created_at
               FROM memories
               WHERE content LIKE ? OR tags LIKE ?
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
    else:
        # 无查询词：返回最重要/最近的
        rows = conn.execute(
            """SELECT id, type, content, tags, importance, created_at
               FROM memories
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    # 更新 last_accessed
    ids = [row["id"] for row in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE memories SET last_accessed = ? WHERE id IN ({placeholders})",
            [now, *ids],
        )
        conn.commit()

    return [dict(row) for row in rows]
