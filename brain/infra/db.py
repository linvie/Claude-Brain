"""SQLite 状态管理 — schema 初始化、连接工厂、查询辅助函数。"""

import sqlite3

from brain.config import DB_PATH


def init_db(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_runs (
            task_id        TEXT PRIMARY KEY,
            project_id     TEXT NOT NULL,
            status         TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            pid            INTEGER,
            start_time     INTEGER,
            end_time       INTEGER
        );

        CREATE TABLE IF NOT EXISTS workspaces (
            project_id     TEXT PRIMARY KEY,
            workspace_path TEXT NOT NULL,
            last_active    INTEGER
        );
        """
    )
    conn.commit()
    _migrate_task_runs(conn)


def _migrate_task_runs(conn: sqlite3.Connection):
    """增量迁移 task_runs 表：添加 task_type, task_name, summary 列。"""
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(task_runs)").fetchall()
    }
    new_columns = {
        "task_type": "TEXT",
        "task_name": "TEXT",
        "summary": "TEXT",
    }
    for col, col_type in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE task_runs ADD COLUMN {col} {col_type}")
    conn.commit()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def has_running_tasks(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_runs WHERE status = 'running'"
    ).fetchone()
    return row["cnt"] > 0


def running_task_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_runs WHERE status = 'running'"
    ).fetchone()
    return row["cnt"]


def project_has_running_task(conn: sqlite3.Connection, project_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_runs WHERE project_id = ? AND status = 'running'",
        (project_id,),
    ).fetchone()
    return row["cnt"] > 0


def all_done(conn: sqlite3.Connection, task_ids: list[str]) -> bool:
    if not task_ids:
        return True
    placeholders = ",".join("?" for _ in task_ids)
    row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM task_runs WHERE task_id IN ({placeholders}) AND status = 'done'",
        task_ids,
    ).fetchone()
    return row["cnt"] == len(task_ids)
