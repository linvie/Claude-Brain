"""inbox / outbox 通信协议 — 构建 inbox、校验和解析 outbox。"""

import json
import logging

from brain.config import REMOTE_ENABLED, REMOTE_HOST, REMOTE_SHARE_DIR

log = logging.getLogger("brain")

VALID_STATUSES = {"TASK_DONE", "TASK_BLOCKED", "TASK_PROGRESS"}


def build_inbox(task: dict, project_info: dict, related_tasks: list[dict]) -> dict:
    """构建完整的 inbox dict。

    Args:
        task: Brain 内部 task dict（来自 Notion 查询）。
        project_info: 项目上下文（project_name, project_description, repo_url）。
        related_tasks: 同项目其他任务摘要列表。

    Returns:
        完整的 inbox dict，由 workspace/setup.py 写入文件。
    """
    # 过滤掉当前任务自身
    other_tasks = [
        {
            "task_name": t["task_name"],
            "status": t["status"],
            "summary": t.get("summary", ""),
        }
        for t in related_tasks
        if t.get("task_id") != task["task_id"]
    ]

    inbox = {
        "task_id": task["task_id"],
        "task_type": task.get("task_type", "executor"),
        "project_id": task["project_id"],
        "project_name": project_info.get("project_name", ""),
        "task_name": task.get("task_name", ""),
        "description": task.get("description", ""),
        "body": task.get("body", ""),
        "priority": task.get("priority", "Normal"),
        "blocked_by": task.get("blocked_by", []),
        "context": {
            "project_description": project_info.get("project_description", ""),
            "repo_url": project_info.get("repo_url"),
            "related_tasks": other_tasks,
        },
    }

    if REMOTE_ENABLED:
        inbox["context"]["remote"] = {
            "enabled": True,
            "host": REMOTE_HOST,
            "share_dir": str(REMOTE_SHARE_DIR),
        }

    return inbox


def validate_outbox(content: str) -> tuple[bool, str]:
    """校验 outbox.json 格式，返回 (is_valid, error_message)。"""
    if not content.strip():
        return False, "outbox.json 为空"

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return False, f"JSON 解析失败: {e}"

    if not isinstance(data, dict):
        return False, "根元素必须是 JSON 对象"

    status = data.get("status")
    if not status or status not in VALID_STATUSES:
        return False, f"status 无效: {status!r}，合法值: {VALID_STATUSES}"

    if not data.get("summary"):
        return False, "缺少 summary 字段或内容为空"

    if status == "TASK_BLOCKED" and not data.get("reason"):
        return False, "TASK_BLOCKED 必须提供 reason 字段"

    if status == "TASK_PROGRESS" and not data.get("stage"):
        return False, "TASK_PROGRESS 必须提供 stage 字段"

    return True, ""


def parse_outbox(content: str) -> dict:
    """解析 outbox.json，返回 dict。调用前应先 validate。"""
    return json.loads(content)
