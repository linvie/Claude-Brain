"""Brain Daemon 主模块 — 主循环、任务分发、watchdog、outbox 处理。"""

import os
import signal
import sqlite3
import time
from pathlib import Path

from brain.config import ACTIVE_INTERVAL, IDLE_INTERVAL, MAX_TASK_DURATION, WORKSPACE_BASE
from brain.db import all_done, get_db, has_running_tasks, project_has_running_task
from brain.logger import log, log_cc, log_scheduler
from brain.notion import append_log, fetch_ready_tasks, update_status
from brain.process import launch_cc
from brain.protocol import parse_outbox, validate_outbox, write_inbox
from brain.workspace import prepare_workspace


# ---------------------------------------------------------------------------
# 任务分发
# ---------------------------------------------------------------------------


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

    # 4. 写入 inbox.json
    write_inbox(workspace, task)

    # 5. 更新 Notion 状态
    update_status(task_id, "Running")

    # 6. 启动 CC
    pid = launch_cc(workspace, task_type, task)

    # 7. 记录到 SQLite
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


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Outbox 处理
# ---------------------------------------------------------------------------


def check_all_outboxes(conn: sqlite3.Connection):
    """轮询所有运行中任务的 outbox.json。"""
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE status = 'running'"
    ).fetchall()

    log.debug("[outbox] 检查 %d 个运行中任务的 outbox", len(rows))

    for task in rows:
        outbox_path = Path(task["workspace_path"]) / "outbox.json"
        if not outbox_path.exists():
            log.debug("[outbox] task=%s, outbox.json 不存在，跳过", task["task_id"])
            continue

        content = outbox_path.read_text(encoding="utf-8")
        if not content.strip() or content.strip() == "{}":
            continue

        log_scheduler.info("收到 outbox: task=%s, 内容长度=%d", task["task_id"], len(content))
        log.debug("[outbox] task=%s, 原始内容:\n%s", task["task_id"], content)

        handle_outbox(conn, task["task_id"], content)

        # 处理完后重置为空 JSON（避免重复处理）
        outbox_path.write_text("{}", encoding="utf-8")


def handle_outbox(conn: sqlite3.Connection, task_id: str, content: str):
    """处理 outbox.json 内容。"""
    now_str = time.strftime("%Y-%m-%d %H:%M")

    is_valid, error_msg = validate_outbox(content)
    if not is_valid:
        log_scheduler.error("outbox 格式异常: task=%s, error=%s", task_id, error_msg)
        log.error("[outbox] 校验失败: task=%s, error=%s, 内容:\n%s", task_id, error_msg, content)
        conn.execute(
            "UPDATE task_runs SET status = 'format_error', end_time = ? WHERE task_id = ?",
            (int(time.time()), task_id),
        )
        conn.commit()
        update_status(task_id, "Blocked")
        append_log(task_id, f"[{now_str}] outbox 格式异常: {error_msg}")
        return

    data = parse_outbox(content)
    status = data["status"]
    summary = data["summary"]
    log_entry = f"[{now_str}] {summary}"

    if status == "TASK_DONE":
        append_log(task_id, log_entry)
        update_status(task_id, "Done")
        end_time = int(time.time())
        conn.execute(
            "UPDATE task_runs SET status = 'done', end_time = ? WHERE task_id = ?",
            (end_time, task_id),
        )
        conn.commit()

        # 计算运行时长
        row = conn.execute(
            "SELECT start_time, workspace_path FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        duration = (end_time - row["start_time"]) // 60 if row else 0
        log_scheduler.info("完成: task=%s, 耗时=%dm, summary=%s", task_id, duration, summary[:100])

        # 更新 workspace last_active
        if row:
            conn.execute(
                """INSERT OR REPLACE INTO workspaces (project_id, workspace_path, last_active)
                   VALUES ((SELECT project_id FROM task_runs WHERE task_id = ?), ?, ?)""",
                (task_id, row["workspace_path"], end_time),
            )
            conn.commit()

    elif status == "TASK_BLOCKED":
        reason = data["reason"]
        append_log(task_id, f"[{now_str}] 阻塞：{reason}")
        update_status(task_id, "Blocked")
        conn.execute(
            "UPDATE task_runs SET status = 'blocked', end_time = ? WHERE task_id = ?",
            (int(time.time()), task_id),
        )
        conn.commit()
        log_scheduler.warning("阻塞: task=%s, reason=%s", task_id, reason)

    elif status == "TASK_PROGRESS":
        stage = data["stage"]
        append_log(task_id, log_entry)
        log_scheduler.info("进度: task=%s, stage=%s, summary=%s", task_id, stage, summary[:100])


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def main():
    log.info("Brain Daemon 启动")
    log.info("配置: idle=%ds, active=%ds, timeout=%ds", IDLE_INTERVAL, ACTIVE_INTERVAL, MAX_TASK_DURATION)
    log.info("Workspace 根目录: %s", WORKSPACE_BASE)

    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    conn = get_db()

    cycle = 0
    try:
        while True:
            cycle += 1
            watchdog(conn)

            if has_running_tasks(conn):
                check_all_outboxes(conn)
                log.debug("[loop] cycle=%d, mode=active, sleep=%ds", cycle, ACTIVE_INTERVAL)
                time.sleep(ACTIVE_INTERVAL)
            else:
                ready_tasks = fetch_ready_tasks()
                if ready_tasks:
                    log_scheduler.info("发现 %d 个 Ready 任务", len(ready_tasks))
                for task in ready_tasks:
                    dispatch(conn, task)
                log.debug("[loop] cycle=%d, mode=idle, ready=%d, sleep=%ds", cycle, len(ready_tasks), IDLE_INTERVAL)
                time.sleep(IDLE_INTERVAL)
    except KeyboardInterrupt:
        log.info("Brain Daemon 收到中断信号，退出")
    finally:
        conn.close()
        log.info("数据库连接已关闭")
