"""Claude Code 执行器 — ClaudeSDKClient 持久会话管理。

每个 channel 维护一个 ClaudeSDKClient 实例。CC 进程在首条消息时启动，
idle 超时后自动关闭。下次消息到来时用 --resume 恢复 session。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from brain.config import SESSION_IDLE_TIMEOUT
from brain.infra.logger import log_cc

# channel_id → _LiveSession
_sessions: dict[str, _LiveSession] = {}


class _LiveSession:
    """一个 channel 的活跃 CC 会话。"""

    def __init__(self, channel_id: str, cwd: Path, system_append: str = ""):
        self.channel_id = channel_id
        self.cwd = cwd
        self.client: ClaudeSDKClient | None = None
        self.session_id: str | None = None
        self.last_activity: float = time.time()
        self._system_append = system_append

    def _build_options(self, resume: str | None = None) -> ClaudeAgentOptions:
        system_prompt = None
        if self._system_append:
            system_prompt = {
                "type": "preset",
                "preset": "claude_code",
                "append": self._system_append,
            }

        return ClaudeAgentOptions(
            cwd=str(self.cwd),
            permission_mode="bypassPermissions",
            system_prompt=system_prompt,
            resume=resume,
            setting_sources=["project", "user"],
        )

    async def query(self, prompt: str, resume: str | None = None) -> tuple[str | None, str]:
        """发送消息并收集结果。返回 (session_id, result_text)。"""
        self.last_activity = time.time()

        options = self._build_options(resume=resume)
        log_cc.info("CC query: channel=%s, cwd=%s, resume=%s", self.channel_id, self.cwd, resume)

        session_id = None
        result_text = ""

        try:
            # 使用 query() 单次调用（SDK 内部管理进程生命周期）
            from claude_agent_sdk import query as sdk_query

            async for message in sdk_query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    session_id = message.session_id
                    result_text = message.result or ""
                    cost = getattr(message, "total_cost_usd", 0) or 0
                    log_cc.info(
                        "CC 完成: session=%s, cost=$%.4f, result=%s",
                        session_id, cost, result_text[:100],
                    )
        except Exception:
            log_cc.exception("CC 执行异常: channel=%s", self.channel_id)
            raise

        self.session_id = session_id
        self.last_activity = time.time()
        return session_id, result_text


async def execute(
    *,
    prompt: str,
    cwd: str | Path,
    channel_id: str,
    system_append: str = "",
    resume: str | None = None,
) -> tuple[str | None, str]:
    """执行 CC 任务，复用或创建 channel 的 session。"""
    cwd = Path(cwd)

    # 获取或创建 live session
    session = _sessions.get(channel_id)
    if session is None or str(session.cwd) != str(cwd):
        session = _LiveSession(channel_id, cwd, system_append)
        _sessions[channel_id] = session
    else:
        # 更新 system_append（记忆可能变了）
        session._system_append = system_append

    return await session.query(prompt, resume=resume)
