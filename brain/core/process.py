"""CC 进程管理 — 启动 Claude Code 子进程。"""

import logging
import subprocess
from pathlib import Path

from brain.config import CONFIG

log_cc = logging.getLogger("brain.cc")


def launch_cc(workspace: Path, task_type: str) -> int:
    """启动 CC 进程，返回 PID。

    CC 通过 CLAUDE.md 知道要读 inbox.json，不需要在命令行传递全部内容。
    权限完全由 --allowedTools / --disallowedTools + .claude/settings.json 控制。
    """
    cmd = ["claude", "--print", "Read inbox.json and follow the instructions in CLAUDE.md."]

    # 从 config 读取角色权限（如果配置了 roles）
    roles_cfg = CONFIG.get("roles", {}).get(task_type, {})
    allowed = roles_cfg.get("allowed_tools", [])
    disallowed = roles_cfg.get("disallowed_tools", [])

    if allowed:
        cmd.extend(["--allowedTools", ",".join(allowed)])
    if disallowed:
        cmd.extend(["--disallowedTools", ",".join(disallowed)])

    log_cc.info("启动 %s CC: workspace=%s", task_type, workspace)
    log_cc.debug("CC 命令: %s", " ".join(cmd))
    if allowed:
        log_cc.debug("allowed_tools: %s", allowed)
    if disallowed:
        log_cc.debug("disallowed_tools: %s", disallowed)

    proc = subprocess.Popen(
        cmd,
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    log_cc.info("%s CC 已启动: PID=%d, workspace=%s", task_type, proc.pid, workspace)
    return proc.pid
