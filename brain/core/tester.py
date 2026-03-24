"""Tester 生命周期管理 — 检测用户停止信号。"""

import sqlite3
import time
from pathlib import Path

from brain.core.process import stop_script
from brain.infra.logger import log_scheduler
from brain.integrations.notion import append_log, get_task_status


def check_tester_stops(conn: sqlite3.Connection):
    """轮询 running tester 任务的 Notion 状态，用户改为 Done 时停止脚本。"""
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE status = 'running' AND task_type = 'tester'"
    ).fetchall()

    for task in rows:
        workspace = Path(task["workspace_path"])
        # 跳过正在生成脚本的 CC 运行（由 outbox 处理）
        if not (workspace / "test_start.sh").exists():
            continue

        notion_status = get_task_status(task["task_id"])
        if notion_status in ("Done", "Blocked"):
            now_str = time.strftime("%Y-%m-%d %H:%M")
            log_scheduler.info("用户停止测试: task=%s, notion_status=%s", task["task_id"], notion_status)
            stop_script(workspace, task["pid"])
            conn.execute(
                "UPDATE task_runs SET status = 'done', end_time = ? WHERE task_id = ?",
                (int(time.time()), task["task_id"]),
            )
            conn.commit()
            append_log(task["task_id"], f"[{now_str}] 用户手动停止测试")
