"""任务分发 — 从 Ready 任务到 CC 启动的完整流程。"""

import sqlite3
import time

from brain.core.process import launch_cc
from brain.core.protocol import build_inbox
from brain.infra.db import all_done, project_has_running_task
from brain.infra.logger import log_scheduler
from brain.integrations.notion import get_page_body, get_project_info, get_related_tasks, update_status
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

    # 4. 获取项目上下文并构建 inbox
    project_info = get_project_info(project_id)
    related_tasks = get_related_tasks(project_id)
    task["body"] = get_page_body(task_id)
    inbox_data = build_inbox(task, project_info, related_tasks)

    # 5. 安装模板 + 写入 inbox.json
    setup_workspace(workspace, task_type, inbox_data, task)

    # 6. 更新 Notion 状态
    update_status(task_id, "Running")

    # 7. 启动 CC
    pid = launch_cc(workspace, task_type)

    # 8. 记录到 SQLite
    start_time = int(time.time())
    conn.execute(
        """INSERT OR REPLACE INTO task_runs
           (task_id, project_id, status, workspace_path, pid, start_time)
           VALUES (?, ?, 'running', ?, ?, ?)""",
        (task_id, project_id, str(workspace), pid, start_time),
    )
    conn.commit()
    log_scheduler.info(
        "分发完成: task=%s, PID=%d, workspace=%s, start_time=%s",
        task_id, pid, workspace, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time)),
    )
