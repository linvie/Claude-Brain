"""任务分发 — 从 Ready 任务到 CC 启动的完整流程。"""

import sqlite3
import time

from brain.core.process import launch_cc, launch_script
from brain.core.protocol import build_inbox
from brain.infra.db import all_done, project_has_running_task
from brain.infra.logger import log_scheduler
from brain.integrations.notion import append_log, get_page_body, get_project_info, get_related_tasks, update_status
from brain.workspace.manager import prepare_workspace
from brain.workspace.setup import setup_workspace


def dispatch(conn: sqlite3.Connection, task: dict):
    """分发一个 Ready 任务。"""
    task_id = task["task_id"]
    project_id = task["project_id"]
    task_type = task.get("task_type", "executor")

    log_scheduler.info("开始分发: task=%s, project=%s, type=%s", task_id, project_id, task_type)

    # 1. 检查依赖
    blocked_by = task.get("blocked_by", [])
    if blocked_by and not all_done(conn, blocked_by):
        log_scheduler.info("跳过: task=%s, 原因=依赖未完成 blocked_by=%s", task_id, blocked_by)
        return

    # 2. 同 project 串行锁
    if project_has_running_task(conn, project_id):
        log_scheduler.info("跳过: task=%s, 原因=项目 %s 已有运行中任务", task_id, project_id)
        return

    # 3. 准备 workspace
    workspace = prepare_workspace(project_id, task.get("repo_url"))

    # 3.5 Tester 快捷路径：脚本已存在 → 直接启动，不走 CC
    if task_type == "tester" and (workspace / "test_start.sh").exists():
        now_str = time.strftime("%Y-%m-%d %H:%M")
        update_status(task_id, "Running")
        pid = launch_script(workspace, "test_start.sh")
        task_name = task.get("task_name", "")
        conn.execute(
            """INSERT OR REPLACE INTO task_runs
               (task_id, project_id, status, workspace_path, pid, start_time, task_type, task_name)
               VALUES (?, ?, 'running', ?, ?, ?, 'tester', ?)""",
            (task_id, project_id, str(workspace), pid, int(time.time()), task_name),
        )
        conn.commit()
        append_log(task_id, f"[{now_str}] 启动测试脚本: PID={pid}")
        log_scheduler.info("Tester 快捷启动: task=%s, PID=%d", task_id, pid)
        return

    # 4. 获取项目上下文并构建 inbox
    project_info = get_project_info(project_id)
    related_tasks = get_related_tasks(project_id)
    task["body"] = get_page_body(task_id)
    project_body = get_page_body(project_id)
    inbox_data = build_inbox(task, project_info, related_tasks)

    # 5. 安装模板 + 写入 inbox.json + 写入 docs/
    setup_workspace(workspace, task_type, inbox_data, task, project_body=project_body)

    # 6. 更新 Notion 状态
    update_status(task_id, "Running")

    # 7. 启动 CC
    pid = launch_cc(workspace, task_type)

    # 8. 记录到 SQLite
    task_name = task.get("task_name", "")
    start_time = int(time.time())
    conn.execute(
        """INSERT OR REPLACE INTO task_runs
           (task_id, project_id, status, workspace_path, pid, start_time, task_type, task_name)
           VALUES (?, ?, 'running', ?, ?, ?, ?, ?)""",
        (task_id, project_id, str(workspace), pid, start_time, task_type, task_name),
    )
    conn.commit()
    log_scheduler.info(
        "分发完成: task=%s, PID=%d, workspace=%s, start_time=%s",
        task_id, pid, workspace, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time)),
    )
