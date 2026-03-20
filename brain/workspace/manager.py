"""Workspace git 管理 — clone / pull / init。"""

import logging
import subprocess
from pathlib import Path

from brain.config import WORKSPACE_BASE

log = logging.getLogger("brain")


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
