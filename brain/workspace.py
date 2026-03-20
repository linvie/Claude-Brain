"""Workspace 管理 — git clone/pull、模板安装。"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

from brain.config import BASE_DIR, CONFIG, WORKSPACE_BASE

log = logging.getLogger("brain")
log_cc = logging.getLogger("brain.cc")


def prepare_workspace(project_id: str, repo_url: str | None) -> Path:
    """准备 workspace：存在则 git pull，不存在则 git clone 或创建空目录。"""
    ws = WORKSPACE_BASE / project_id
    if ws.exists():
        log.info("[workspace] 已存在，执行 git pull: %s", ws)
        result = subprocess.run(["git", "pull"], cwd=ws, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("[workspace] git pull 失败: %s", result.stderr.strip())
        else:
            log.debug("[workspace] git pull 输出: %s", result.stdout.strip())
    elif repo_url:
        log.info("[workspace] 克隆仓库 %s → %s", repo_url, ws)
        result = subprocess.run(["git", "clone", repo_url, str(ws)], capture_output=True, text=True)
        if result.returncode != 0:
            log.error("[workspace] git clone 失败: %s", result.stderr.strip())
        else:
            log.info("[workspace] 克隆完成")
    else:
        log.info("[workspace] 创建新项目: %s", ws)
        ws.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=ws, capture_output=True)

    return ws


def install_template(workspace: Path, task_type: str, task: dict | None = None):
    """将角色模板目录和共享文件复制到 workspace。

    复制顺序：shared/ → {task_type}/ → 覆盖写入。

    对 planner 类型，额外注入 brain_config.json 供 CC 读取数据库 ID。
    """
    shared_dir = BASE_DIR / "templates" / "shared"
    role_dir = BASE_DIR / "templates" / task_type

    if not role_dir.exists():
        log_cc.error("角色模板目录不存在: %s", role_dir)
        return

    # 复制共享模板（outbox.json、OUTBOX_FORMAT.md）
    if shared_dir.exists():
        for src in shared_dir.iterdir():
            dest = workspace / src.name
            if src.is_file():
                shutil.copy2(src, dest)
                log_cc.debug("复制共享文件: %s → %s", src.name, dest)

    # 复制角色模板（CLAUDE.md、.claude/settings.json、WORKFLOW.md 等）
    for src in role_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(role_dir)
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log_cc.debug("复制角色文件: %s → %s", rel, dest)

    # Planner 需要知道 Notion 数据库 ID 和 project_id 以创建 Task
    if task_type == "planner" and task:
        notion_cfg = CONFIG["notion"]
        brain_config = {
            "task_db_id": notion_cfg["task_db_id"],
            "project_db_id": notion_cfg["project_db_id"],
            "project_id": task.get("project_id", ""),
        }
        config_path = workspace / "brain_config.json"
        config_path.write_text(json.dumps(brain_config, indent=2), encoding="utf-8")
        log_cc.debug("注入 brain_config.json: %s", brain_config)

    log_cc.info("模板安装完成: type=%s, workspace=%s", task_type, workspace)
