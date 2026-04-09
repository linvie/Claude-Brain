"""Claude Code 执行器 — 封装 Claude Agent SDK 调用。"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

from brain.infra.logger import log, log_cc


async def execute(
    *,
    prompt: str,
    cwd: str | Path,
    system_append: str = "",
    resume: str | None = None,
) -> tuple[str | None, str]:
    """执行 CC 任务。

    Args:
        prompt: 用户消息
        cwd: workspace 目录
        system_append: 追加到 system prompt 的内容（记忆注入等）
        resume: session_id，传入则恢复已有会话

    Returns:
        (session_id, result_text)
    """
    system_prompt: dict | None = None
    if system_append:
        system_prompt = {
            "type": "preset",
            "preset": "claude_code",
            "append": system_append,
        }

    options = ClaudeAgentOptions(
        cwd=str(cwd),
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        resume=resume,
        setting_sources=["project", "user"],
    )

    log_cc.info("[cc] 启动: cwd=%s, resume=%s, prompt=%s", cwd, resume, prompt[:80])

    session_id = None
    result_text = ""

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                result_text = message.result or ""
                log_cc.info(
                    "[cc] 完成: session=%s, cost=$%.4f, result=%s",
                    session_id,
                    getattr(message, "total_cost_usd", 0) or 0,
                    result_text[:100],
                )
    except Exception:
        log_cc.exception("[cc] 执行异常")
        raise

    return session_id, result_text
