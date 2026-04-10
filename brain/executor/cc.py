"""飞书对话的 CC 执行器 — ClaudeSDKClient 持久会话。

每个 channel 维护一个 ClaudeSDKClient 实例，CC 进程保持运行。
idle 超时后自动 disconnect，下次消息用 --resume 恢复。

注意：此模块只用于飞书对话流（v2）。
Notion 任务流（v1）使用 brain/core/process.py（CLI subprocess），不受影响。
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
    """一个 channel 的持久 CC 会话。

    生命周期：
    - connect：首条消息到达时，启动 CC 进程
    - query：后续消息复用已连接的 client（热响应）
    - idle 超时：_idle_watcher 自动 disconnect
    - 下次消息：重新 connect（用 resume 恢复上下文）
    """

    def __init__(self, channel_id: str, cwd: Path, system_append: str = ""):
        self.channel_id = channel_id
        self.cwd = cwd
        self.client: ClaudeSDKClient | None = None
        self.session_id: str | None = None
        self.last_activity: float = 0
        self._system_append = system_append
        self._idle_task: asyncio.Task | None = None
        self._connected = False

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

    async def _ensure_connected(self, resume: str | None = None):
        """确保 client 已连接。未连接则创建新连接。"""
        if self._connected and self.client:
            return

        options = self._build_options(resume=resume)
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        self._connected = True

        # 启动 idle 监视器
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_watcher())

        log_cc.info("CC 已连接: channel=%s, cwd=%s, resume=%s",
                    self.channel_id, self.cwd, resume)

    async def _disconnect(self):
        """断开连接，释放 CC 进程。"""
        if self.client and self._connected:
            try:
                await self.client.disconnect()
            except Exception:
                log_cc.debug("CC disconnect 异常（忽略）: channel=%s", self.channel_id)
            self._connected = False
            log_cc.info("CC 已断开: channel=%s, idle 超时", self.channel_id)

    async def _idle_watcher(self):
        """监视 idle 超时，自动断开连接。"""
        while self._connected:
            await asyncio.sleep(60)  # 每分钟检查一次
            if self._connected and time.time() - self.last_activity > SESSION_IDLE_TIMEOUT:
                await self._disconnect()
                return

    async def query(self, prompt: str, resume: str | None = None) -> tuple[str | None, str]:
        """发送消息并收集结果。

        如果 client 已连接，直接发送（热响应）。
        如果未连接，先 connect（可能用 resume 恢复）。
        """
        self.last_activity = time.time()

        try:
            await self._ensure_connected(resume=resume)
        except Exception:
            log_cc.exception("CC 连接失败: channel=%s", self.channel_id)
            # fallback 到一次性 query
            return await self._fallback_query(prompt, resume)

        log_cc.info("CC query: channel=%s, connected=%s, prompt=%s",
                    self.channel_id, self._connected, prompt[:80])

        session_id = None
        result_text = ""

        try:
            await self.client.query(prompt)
            async for message in self.client.receive_response():
                if isinstance(message, ResultMessage):
                    session_id = message.session_id
                    result_text = message.result or ""
                    cost = getattr(message, "total_cost_usd", 0) or 0
                    log_cc.info(
                        "CC 完成: session=%s, cost=$%.4f, result=%s",
                        session_id, cost, result_text[:100],
                    )
        except Exception:
            log_cc.exception("CC query 异常: channel=%s", self.channel_id)
            # 连接可能断了，标记为未连接
            self._connected = False
            raise

        self.session_id = session_id
        self.last_activity = time.time()
        return session_id, result_text

    async def _fallback_query(self, prompt: str, resume: str | None) -> tuple[str | None, str]:
        """连接失败时的 fallback：一次性 query（和之前行为一致）。"""
        from claude_agent_sdk import query as sdk_query

        log_cc.info("CC fallback query: channel=%s", self.channel_id)
        options = self._build_options(resume=resume)

        session_id = None
        result_text = ""

        async for message in sdk_query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                result_text = message.result or ""

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
    """执行 CC 任务，复用或创建 channel 的持久会话。"""
    cwd = Path(cwd)

    session = _sessions.get(channel_id)
    if session is None or str(session.cwd) != str(cwd):
        session = _LiveSession(channel_id, cwd, system_append)
        _sessions[channel_id] = session
    else:
        session._system_append = system_append

    return await session.query(prompt, resume=resume)
