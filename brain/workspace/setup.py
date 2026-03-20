"""Workspace 模板安装 + 上下文注入。"""

import json
import logging
import shutil
from pathlib import Path

from brain.config import BASE_DIR, CONFIG

log_cc = logging.getLogger("brain.cc")


def setup_workspace(workspace: Path, task_type: str, inbox_data: dict, task: dict):
    """一站式 workspace 准备：安装模板 + 写入 inbox.json + 注入配置。

    Args:
        workspace: workspace 目录路径。
        task_type: 角色类型（planner / executor）。
        inbox_data: 完整的 inbox dict（由 protocol.build_inbox 构建）。
        task: Brain 内部 task dict（用于 planner 配置注入）。
    """
    _install_shared_template(workspace)
    _install_role_template(workspace, task_type)
    _write_inbox(workspace, inbox_data)
    _write_git_exclude(workspace)

    if task_type == "planner":
        _inject_brain_config(workspace, task)

    log_cc.info("workspace 准备完成: type=%s, workspace=%s", task_type, workspace)


def _install_shared_template(workspace: Path):
    """复制 shared/ 模板（outbox.json、OUTBOX_FORMAT.md、WORKFLOW.md 等）。"""
    shared_dir = BASE_DIR / "templates" / "shared"
    if not shared_dir.exists():
        return

    for src in shared_dir.iterdir():
        dest = workspace / src.name
        if src.is_file():
            shutil.copy2(src, dest)
            log_cc.debug("复制共享文件: %s → %s", src.name, dest)


def _install_role_template(workspace: Path, task_type: str):
    """复制角色模板（CLAUDE.md、.claude/settings.json 等）。"""
    role_dir = BASE_DIR / "templates" / task_type
    if not role_dir.exists():
        log_cc.error("角色模板目录不存在: %s", role_dir)
        return

    for src in role_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(role_dir)
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log_cc.debug("复制角色文件: %s → %s", rel, dest)


def _write_inbox(workspace: Path, inbox_data: dict):
    """将完整 inbox dict 写入 workspace/inbox.json。"""
    inbox_path = workspace / "inbox.json"
    inbox_path.write_text(
        json.dumps(inbox_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log_cc.info("已写入 inbox.json: task_id=%s", inbox_data.get("task_id"))


# Brain 注入的文件列表，需要从 workspace git 中排除
_BRAIN_INJECTED_FILES = [
    "inbox.json",
    "outbox.json",
    "brain_config.json",
    "CLAUDE.md",
    "WORKFLOW.md",
    "OUTBOX_FORMAT.md",
    "validate_outbox.py",
    ".claude/",
]


def _write_git_exclude(workspace: Path):
    """将 Brain 注入文件写入 .git/info/exclude，避免被 CC commit。"""
    exclude_path = workspace / ".git" / "info" / "exclude"
    if not exclude_path.parent.exists():
        return  # 非 git 仓库，跳过

    marker = "# Brain daemon injected files"
    # 如果已有 Brain 标记，不重复写入
    if exclude_path.exists():
        existing = exclude_path.read_text(encoding="utf-8")
        if marker in existing:
            return

    entries = "\n".join(_BRAIN_INJECTED_FILES)
    block = f"\n{marker}\n{entries}\n"

    with open(exclude_path, "a", encoding="utf-8") as f:
        f.write(block)

    log_cc.debug("已写入 .git/info/exclude: %d 条规则", len(_BRAIN_INJECTED_FILES))


def _inject_brain_config(workspace: Path, task: dict):
    """为 planner 注入 brain_config.json（Notion 数据库 ID + project_id）。"""
    notion_cfg = CONFIG["notion"]
    brain_config = {
        "task_db_id": notion_cfg["task_db_id"],
        "project_db_id": notion_cfg["project_db_id"],
        "project_id": task.get("project_id", ""),
    }
    config_path = workspace / "brain_config.json"
    config_path.write_text(
        json.dumps(brain_config, indent=2),
        encoding="utf-8",
    )
    log_cc.debug("注入 brain_config.json: %s", brain_config)
