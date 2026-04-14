"""记忆存储 — SQLite CRUD + FTS5 全文搜索。"""

from __future__ import annotations

import json
import sqlite3
import time

from brain.infra.logger import log_memory as log


def init_memory_tables(conn: sqlite3.Connection):
    """创建 memories 表 + FTS5 索引，执行增量迁移。"""
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
    _migrate_memories(conn)


def _migrate_memories(conn: sqlite3.Connection):
    """增量迁移：scope 字段 + FTS5 虚拟表 + 同步触发器。"""
    # 1. 添加 scope 列（如果不存在）
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    if "scope" not in existing:
        conn.execute("ALTER TABLE memories ADD COLUMN scope TEXT DEFAULT 'global'")
        conn.commit()
        log.info("[memory] 迁移: 添加 scope 列")

    # 2. 创建 FTS5 虚拟表（content-sync 模式，外部内容来自 memories）
    # 检查 FTS5 表是否已存在
    fts_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    if not fts_exists:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                content,
                tags,
                content=memories,
                content_rowid=id,
                tokenize="trigram"
            );

            -- 触发器：memories 写入时同步 FTS5
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, tags)
                VALUES (new.id, new.content, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, tags)
                VALUES ('delete', old.id, old.content, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, tags)
                VALUES ('delete', old.id, old.content, old.tags);
                INSERT INTO memories_fts(rowid, content, tags)
                VALUES (new.id, new.content, new.tags);
            END;
            """
        )
        conn.commit()
        log.info("[memory] 迁移: 创建 FTS5 虚拟表 + 触发器")

        # 3. 回填现有 memories 到 FTS5 索引
        count = conn.execute(
            "INSERT INTO memories_fts(rowid, content, tags) "
            "SELECT id, content, tags FROM memories"
        ).rowcount
        conn.commit()
        if count:
            log.info("[memory] 迁移: 回填 %d 条记忆到 FTS5", count)


def add_memory(
    conn: sqlite3.Connection,
    *,
    type: str,
    content: str,
    source: str | None = None,
    tags: list[str] | None = None,
    importance: int = 5,
    scope: str = "global",
) -> int:
    """添加一条记忆，返回 id。"""
    now = int(time.time())
    cursor = conn.execute(
        """INSERT INTO memories (type, content, source, tags, importance, last_accessed, created_at, scope)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (type, content, source, json.dumps(tags or []), importance, now, now, scope),
    )
    conn.commit()
    log.debug("[memory] 添加: type=%s, scope=%s, content=%s", type, scope, content[:60])
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
