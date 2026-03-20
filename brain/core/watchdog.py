"""Watchdog — 超时检测 + 进程健康检查。"""

import os
import signal
import sqlite3
import time

from brain.config import MAX_TASK_DURATION
from brain.infra.logger import log, log_cc, log_scheduler
from brain.integrations.notion import append_log, update_status


def watchdog(conn: sqlite3.Connection):
    """检测超时任务并终止。"""
    now = int(time.time())
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE status = 'running'"
    ).fetchall()

    for task in rows:
        elapsed = now - task["start_time"]
        task_id = task["task_id"]
        pid = task["pid"]

        # 检查进程是否还存活
        try:
            os.kill(pid, 0)  # signal 0 不发信号，仅检查进程是否存在
        except ProcessLookupError:
            log_scheduler.warning("进程已消失: task=%s, PID=%d, elapsed=%dm，标记为异常退出", task_id, pid, elapsed // 60)
            log_cc.warning("CC 进程异常退出: PID=%d, task=%s", pid, task_id)
            conn.execute(
                "UPDATE task_runs SET status = 'format_error', end_time = ? WHERE task_id = ?",
                (now, task_id),
            )
            conn.commit()
            update_status(task_id, "Blocked")
            append_log(task_id, f"[{time.strftime('%Y-%m-%d %H:%M')}] CC 进程异常退出，需人工检查")
            continue

        if elapsed > MAX_TASK_DURATION:
            log_scheduler.warning("超时: task=%s, PID=%d, elapsed=%dm (max=%dm)", task_id, pid, elapsed // 60, MAX_TASK_DURATION // 60)
            log_cc.warning("终止超时 CC 进程: PID=%d, task=%s", pid, task_id)

            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            conn.execute(
                "UPDATE task_runs SET status = 'timeout', end_time = ? WHERE task_id = ?",
                (now, task_id),
            )
            conn.commit()

            update_status(task_id, "Timeout")
            append_log(
                task_id, f"[{time.strftime('%Y-%m-%d %H:%M')}] 任务超时（{elapsed // 60}分钟），已终止"
            )
        else:
            log.debug("[watchdog] task=%s, PID=%d, 已运行 %dm/%dm", task_id, pid, elapsed // 60, MAX_TASK_DURATION // 60)
