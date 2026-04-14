"""Outbox 处理 — 轮询运行中任务的 outbox.json 并处理结果。"""

import os
import shutil
import signal
import sqlite3
import time
from pathlib import Path

from brain.config import REMOTE_ENABLED, REMOTE_HOST, REMOTE_SHARE_DIR
from brain.core.protocol import parse_outbox, validate_outbox
from brain.infra.logger import log, log_cc, log_scheduler
from brain.integrations.notion import append_log, update_status

_HISTORY_SEPARATOR = "\n"


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
        _kill_cc_process(conn, task_id)
        append_log(task_id, log_entry)

        # 提取 test_instructions 并追加到 Notion execution_log
        test_instructions = data.get("test_instructions", "")
        if test_instructions:
            # 远程模式：替换 localhost URL 为 remote host
            if REMOTE_ENABLED:
                test_instructions = test_instructions.replace("localhost", REMOTE_HOST)
                test_instructions = test_instructions.replace("127.0.0.1", REMOTE_HOST)
            append_log(task_id, f"[{now_str}] 测试方法:\n{test_instructions}")

        # 提取 pr_url 并追加到 Notion
        pr_url = data.get("pr_url", "")
        if pr_url:
            append_log(task_id, f"[{now_str}] PR 已创建: {pr_url}")

        update_status(task_id, "Done")
        end_time = int(time.time())
        conn.execute(
            "UPDATE task_runs SET status = 'done', end_time = ?, summary = ? WHERE task_id = ?",
            (end_time, summary, task_id),
        )
        conn.commit()

        # 计算运行时长 + 写 history.md
        row = conn.execute(
            "SELECT start_time, workspace_path, task_name FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        duration = (end_time - row["start_time"]) // 60 if row else 0
        log_scheduler.info("完成: task=%s, 耗时=%dm, summary=%s", task_id, duration, summary[:100])

        # 追加 docs/history.md
        if row and row["workspace_path"]:
            _append_history(Path(row["workspace_path"]), row["task_name"] or task_id, now_str, summary)

        # 远程模式：复制产物到 share_dir（兜底，即使 CC 已复制）
        if REMOTE_ENABLED and data.get("artifacts") and row and row["workspace_path"]:
            _copy_artifacts_to_share(Path(row["workspace_path"]), data["artifacts"], task_id, now_str)

        # 更新 workspace last_active
        if row:
            conn.execute(
                """INSERT OR REPLACE INTO workspaces (project_id, workspace_path, last_active)
                   VALUES ((SELECT project_id FROM task_runs WHERE task_id = ?), ?, ?)""",
                (task_id, row["workspace_path"], end_time),
            )
            conn.commit()

        # 飞书通知（含 PR 链接）
        task_name = row["task_name"] if row else task_id
        notify_summary = summary
        if pr_url:
            notify_summary = f"{summary}\n\nPR: {pr_url}"
        _notify_feishu("TASK_DONE", task_name, notify_summary)

    elif status == "TASK_BLOCKED":
        _kill_cc_process(conn, task_id)
        reason = data["reason"]
        append_log(task_id, f"[{now_str}] 阻塞：{reason}")
        update_status(task_id, "Blocked")
        conn.execute(
            "UPDATE task_runs SET status = 'blocked', end_time = ? WHERE task_id = ?",
            (int(time.time()), task_id),
        )
        conn.commit()
        log_scheduler.warning("阻塞: task=%s, reason=%s", task_id, reason)

        # 飞书通知
        row = conn.execute(
            "SELECT task_name FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        task_name = row["task_name"] if row else task_id
        _notify_feishu("TASK_BLOCKED", task_name, reason)

    elif status == "TASK_PROGRESS":
        stage = data["stage"]
        append_log(task_id, log_entry)
        log_scheduler.info("进度: task=%s, stage=%s, summary=%s", task_id, stage, summary[:100])


def _notify_feishu(status: str, task_name: str, summary: str):  # pragma: no cover
    """发送飞书通知（非阻塞，失败静默）。"""
    from brain.infra.feishu_notify import notify_feishu

    emoji = "✅" if status == "TASK_DONE" else "🚫"
    title = f"{emoji} {status}"
    content = f"任务: {task_name}\n\n{summary[:500]}"
    notify_feishu(title, content)


def _kill_cc_process(conn: sqlite3.Connection, task_id: str):
    """终止 CC 进程，防止已完成任务的 CC 继续写 outbox。"""
    row = conn.execute(
        "SELECT pid FROM task_runs WHERE task_id = ?", (task_id,)
    ).fetchone()
    if not row or not row["pid"]:
        return
    pid = row["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        log_cc.info("已终止 CC 进程: PID=%d, task=%s", pid, task_id)
    except ProcessLookupError:
        pass  # 进程已退出


def _copy_artifacts_to_share(workspace: Path, artifacts: list, task_id: str, now_str: str):
    """远程模式：将产物复制到 share_dir，按项目名分目录。"""
    try:
        REMOTE_SHARE_DIR.mkdir(parents=True, exist_ok=True)
        for artifact in artifacts:
            src = workspace / artifact
            if src.exists():
                dst = REMOTE_SHARE_DIR / src.name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                log.info("[outbox] 产物已复制: %s → %s", src, dst)
            else:
                log.warning("[outbox] 产物不存在，跳过: %s", src)
        append_log(task_id, f"[{now_str}] 产物已复制到: {REMOTE_SHARE_DIR}")
    except OSError as e:
        log.error("[outbox] 复制产物失败: %s", e)


def _append_history(workspace: Path, task_name: str, now_str: str, summary: str):
    """将完成记录追加到 docs/history.md。"""
    history_path = workspace / "docs" / "history.md"
    history_path.parent.mkdir(exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(f"{_HISTORY_SEPARATOR}## {task_name} ({now_str})\n\n{summary}\n")
