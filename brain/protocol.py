"""inbox / outbox 通信协议 — 读写 inbox.json、校验和解析 outbox.json。"""

import json
import logging
from pathlib import Path

log = logging.getLogger("brain")

VALID_STATUSES = {"TASK_DONE", "TASK_BLOCKED", "TASK_PROGRESS"}


def write_inbox(workspace: Path, task: dict):
    """向 workspace/inbox.json 写入任务描述。"""
    inbox_data = {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "project_id": task["project_id"],
        "description": task["description"],
    }
    if task.get("context"):
        inbox_data["context"] = task["context"]

    inbox_path = workspace / "inbox.json"
    inbox_path.write_text(json.dumps(inbox_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已写入 inbox.json: task_id=%s", task["task_id"])


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
