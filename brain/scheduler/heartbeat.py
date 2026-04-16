"""Heartbeat 心跳机制 — 定期执行系统健康检查。

合并 HEARTBEAT_SYSTEM.md（内建）和 HEARTBEAT.md（用户自定义）作为 prompt，
用 Haiku 执行检查。结果有需要通知的内容才推送飞书，无事则静默。
"""

from __future__ import annotations

import logging
from pathlib import Path

from brain.config import RESOURCE_DIR

log = logging.getLogger("brain.heartbeat")

# 模板文件（打包在 brain/data/template/ 中）
_SYSTEM_TEMPLATE = RESOURCE_DIR / "template" / "HEARTBEAT_SYSTEM.md"

# 静默关键词：心跳结果包含此词时不推送通知
NO_ACTION_MARKER = "NO_ACTION"


def build_heartbeat_prompt(workspace: Path) -> str:
    """合并 HEARTBEAT_SYSTEM.md 和 HEARTBEAT.md，构建心跳 prompt。

    Args:
        workspace: v2 workspace 路径，其中可能包含用户自定义的 HEARTBEAT.md。

    Returns:
        合并后的 prompt 文本。
    """
    parts: list[str] = []

    # 1. 内建系统检查（从模板目录读取）
    if _SYSTEM_TEMPLATE.exists():
        parts.append(_SYSTEM_TEMPLATE.read_text(encoding="utf-8"))
    else:
        log.warning("HEARTBEAT_SYSTEM.md 不存在: %s", _SYSTEM_TEMPLATE)

    # 2. 用户自定义检查（从 workspace 读取）
    user_heartbeat = workspace / "HEARTBEAT.md"
    if user_heartbeat.exists():
        content = user_heartbeat.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)

    return "\n\n---\n\n".join(parts)


async def run_heartbeat(workspace: Path) -> str | None:
    """启动隔离 CC session 执行心跳检查。

    Args:
        workspace: v2 workspace 路径。

    Returns:
        检查结果文本。无事发生时返回 None。
    """
    from brain.executor.cc import one_shot_query

    prompt = build_heartbeat_prompt(workspace)
    if not prompt.strip():
        log.warning("心跳 prompt 为空，跳过")
        return None

    log.info("执行心跳检查: workspace=%s", workspace.name)

    result = await one_shot_query(
        prompt="请执行心跳检查。",
        cwd=workspace,
        system_append=prompt,
        timeout=90.0,
    )

    if not result or NO_ACTION_MARKER in result:
        log.info("心跳检查完成: 无需通知")
        return None

    log.info("心跳检查完成: 有需要通知的内容 (%d 字符)", len(result))
    return result


async def run_heartbeat_and_notify(workspace: Path) -> None:
    """执行心跳检查，有结果则推送飞书通知。"""
    try:
        result = await run_heartbeat(workspace)
        if result:
            from brain.infra.feishu_notify import notify_feishu
            notify_feishu("Heartbeat 巡检报告", result)
    except Exception:
        log.exception("心跳任务异常")
