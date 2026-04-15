"""Workspace 模板安装 + 上下文注入。"""

import json
import logging
import shutil
from pathlib import Path

from brain.config import CONFIG, FEISHU_ENABLED, FEISHU_NOTIFY_CHAT_ID, RESOURCE_DIR

log_cc = logging.getLogger("brain.cc")

# v1 模板目录（brain/data/v1_templates/）— 打包在 wheel 中
_V1_TEMPLATE_DIR = RESOURCE_DIR / "v1_templates"

# 标记分区常量（与 session/manager.py 一致）
_TEMPLATE_START = "<!-- CCBRAIN_TEMPLATE_START -->"
_TEMPLATE_END = "<!-- CCBRAIN_TEMPLATE_END -->"


def setup_workspace(workspace: Path, task_type: str, inbox_data: dict, task: dict, *, project_body: str = ""):
    """一站式 workspace 准备：安装模板 + 写入 inbox.json + 注入配置。

    Args:
        workspace: workspace 目录路径。
        task_type: 角色类型（planner / executor）。
        inbox_data: 完整的 inbox dict（由 protocol.build_inbox 构建）。
        task: Brain 内部 task dict（用于 planner 配置注入）。
        project_body: Project 页面正文，写入 docs/requirements.md。
    """
    _install_shared_template(workspace)
    _install_role_template(workspace, task_type)
    _write_inbox(workspace, inbox_data)
    _write_git_exclude(workspace)
    _write_project_docs(workspace, project_body)

    if task_type == "planner":
        _inject_brain_config(workspace, task)

    # 注入飞书通知 chat_id（所有角色都可以主动通知用户）
    if FEISHU_ENABLED and FEISHU_NOTIFY_CHAT_ID:
        _inject_feishu_notify(workspace)

    log_cc.info("workspace 准备完成: type=%s, workspace=%s", task_type, workspace)


def _install_shared_template(workspace: Path):
    """复制 shared/ 模板（outbox.json、OUTBOX_FORMAT.md、WORKFLOW.md 等）。"""
    shared_dir = _V1_TEMPLATE_DIR / "shared"
    if not shared_dir.exists():
        return

    for src in shared_dir.iterdir():
        dest = workspace / src.name
        if src.is_file():
            shutil.copy2(src, dest)
            log_cc.debug("复制共享文件: %s → %s", src.name, dest)


def _install_role_template(workspace: Path, task_type: str):
    """安装角色模板（CLAUDE.md 用标记合并，其他文件直接复制）。"""
    role_dir = _V1_TEMPLATE_DIR / task_type
    if not role_dir.exists():
        log_cc.error("角色模板目录不存在: %s", role_dir)
        return

    for src in role_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(role_dir)
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel.name == "CLAUDE.md":
                _merge_claude_md(src, dest)
            else:
                shutil.copy2(src, dest)
            log_cc.debug("安装角色文件: %s → %s", rel, dest)


def _merge_claude_md(template_src: Path, dest: Path):
    """标记合并 CLAUDE.md：替换模板区域，保留用户/CC 自定义内容。"""
    new_template = template_src.read_text(encoding="utf-8")

    if not dest.exists():
        dest.write_text(new_template, encoding="utf-8")
        return

    existing = dest.read_text(encoding="utf-8")

    if _TEMPLATE_START in existing and _TEMPLATE_END in existing:
        # 替换标记之间的内容，保留标记之后的用户内容
        before_start = existing.split(_TEMPLATE_START)[0]
        after_end = existing.split(_TEMPLATE_END)[1]
        if _TEMPLATE_START in new_template and _TEMPLATE_END in new_template:
            new_section = new_template[
                new_template.index(_TEMPLATE_START):new_template.index(_TEMPLATE_END) + len(_TEMPLATE_END)
            ]
        else:
            new_section = new_template
        result = before_start + new_section + after_end
    else:
        # 旧格式（无标记），全部替换为新模板
        result = new_template

    dest.write_text(result, encoding="utf-8")


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
    "docs/",
    "test_start.sh",
    "test_stop.sh",
]


def _write_project_docs(workspace: Path, project_body: str):
    """将 Project 页面正文写入 docs/requirements.md。"""
    if not project_body:
        return
    docs_dir = workspace / "docs"
    docs_dir.mkdir(exist_ok=True)
    req_path = docs_dir / "requirements.md"
    req_path.write_text(project_body, encoding="utf-8")
    log_cc.info("已写入 docs/requirements.md: %d 字符", len(project_body))


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


def _inject_feishu_notify(workspace: Path):
    """注入飞书通知 chat_id 到 CLAUDE.md 末尾，使 CC 能主动发消息。"""
    claude_md = workspace / "CLAUDE.md"
    if not claude_md.exists():
        return
    content = claude_md.read_text(encoding="utf-8")
    if "lark-cli im send" in content:
        return  # 已有通知指引
    notify_block = (
        "\n\n## 飞书通知\n\n"
        "如果遇到阻碍、需要用户确认、或有阶段性成果，使用 lark-cli 发送消息：\n"
        "```bash\n"
        f'lark-cli im send --receive-id "{FEISHU_NOTIFY_CHAT_ID}" '
        '--receive-id-type chat_id --msg-type text '
        '--content \'{"text":"你的消息"}\'\n'
        "```\n"
    )
    claude_md.write_text(content + notify_block, encoding="utf-8")
    log_cc.debug("注入飞书通知指引: chat_id=%s", FEISHU_NOTIFY_CHAT_ID)
