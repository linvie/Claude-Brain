"""Workspace git 管理 — clone / pull / copy / init。"""

import logging
import shutil
import subprocess
from pathlib import Path

from brain.config import WORKSPACE_BASE

log = logging.getLogger("brain")


def _is_remote_url(url: str) -> bool:
    """判断是远程 URL（git clone）还是本地路径（cp -r）。"""
    return url.startswith("https://") or url.startswith("http://") or url.startswith("git@")


def prepare_workspace(project_id: str, repo_url: str | None) -> Path:  # pragma: no cover
    """准备 workspace：存在则 git pull，不存在则 clone / copy / init。

    repo_url 支持：
    - GitHub URL（https://... 或 git@...）→ git clone
    - 本地路径（/path/to/repo 或 ~/path）→ cp -r
    - None → 空目录 + git init
    """
    ws = WORKSPACE_BASE / project_id
    if ws.exists():
        log.info("[workspace] 已存在，执行 git pull: %s", ws)
        result = subprocess.run(["git", "pull"], cwd=ws, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("[workspace] git pull 失败: %s", result.stderr.strip())
        else:
            log.debug("[workspace] git pull 输出: %s", result.stdout.strip())
    elif repo_url and _is_remote_url(repo_url):
        log.info("[workspace] 克隆仓库 %s → %s", repo_url, ws)
        result = subprocess.run(["git", "clone", repo_url, str(ws)], capture_output=True, text=True)
        if result.returncode != 0:
            log.error("[workspace] git clone 失败: %s", result.stderr.strip())
        else:
            log.info("[workspace] 克隆完成")
    elif repo_url:
        # 本地路径：展开 ~ 并复制
        src = Path(repo_url).expanduser()
        if src.is_dir():
            log.info("[workspace] 复制本地仓库 %s → %s", src, ws)
            shutil.copytree(src, ws, dirs_exist_ok=True)
            log.info("[workspace] 本地复制完成")
        else:
            log.error("[workspace] 本地路径不存在或非目录: %s", src)
            ws.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    else:
        log.info("[workspace] 创建新项目: %s", ws)
        ws.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=ws, capture_output=True)

    return ws
