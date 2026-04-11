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
# channel_id → model（session 不存在时暂存）
_model_overrides: dict[str, str | None] = {}


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
        self.model: str | None = None  # 当前模型（用户可通过 /model 切换）
        self.total_cost: float = 0.0   # 累计费用
        self.total_queries: int = 0    # 累计查询数

    def _build_options(self, resume: str | None = None) -> ClaudeAgentOptions:
        # 始终使用 claude_code preset，确保 CC 完整加载
        # CLAUDE.md、skills、hooks、commands 等 workspace 配置
        system_prompt: dict = {
            "type": "preset",
            "preset": "claude_code",
        }
        if self._system_append:
            system_prompt["append"] = self._system_append

        opts = ClaudeAgentOptions(
            cwd=str(self.cwd),
            permission_mode="bypassPermissions",
            system_prompt=system_prompt,
            resume=resume,
            model=self.model,
            # project: workspace/.claude/settings.json
            # user: ~/.claude/settings.json
            # local: workspace/.claude/settings.local.json
            setting_sources=["project", "user", "local"],
        )
        log_cc.info("CC options: model=%s, resume=%s, cwd=%s",
                    self.model, resume, self.cwd)
        return opts

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

    async def query(
        self,
        prompt: str,
        resume: str | None = None,
        on_stream: asyncio.coroutines = None,
    ) -> tuple[str | None, str]:
        """发送消息并收集结果，支持流式回调。

        Args:
            on_stream: async callable(text: str)，CC 每产出一段文本时调用
        """
        self.last_activity = time.time()

        try:
            await self._ensure_connected(resume=resume)
        except Exception:
            log_cc.exception("CC 连接失败: channel=%s", self.channel_id)
            return await self._fallback_query(prompt, resume, on_stream)

        log_cc.info("CC query: channel=%s, connected=%s, prompt=%s",
                    self.channel_id, self._connected, prompt[:80])

        session_id = None
        result_text = ""
        streaming_text = ""
        last_stream_time = 0.0

        try:
            await self.client.query(prompt)
            async for message in self.client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            streaming_text += block.text
                            # 节流：每 2 秒推送一次
                            now = time.time()
                            if on_stream and now - last_stream_time > 2:
                                last_stream_time = now
                                try:
                                    await on_stream(streaming_text)
                                except Exception:
                                    pass  # 流式更新失败不影响执行

                elif isinstance(message, ResultMessage):
                    session_id = message.session_id
                    result_text = message.result or ""
                    cost = getattr(message, "total_cost_usd", 0) or 0
                    self.total_cost += cost
                    self.total_queries += 1
                    log_cc.info(
                        "CC 完成: session=%s, cost=$%.4f, result=%s",
                        session_id, cost, result_text[:100],
                    )
        except Exception:
            log_cc.exception("CC query 异常: channel=%s", self.channel_id)
            self._connected = False
            raise

        self.session_id = session_id
        self.last_activity = time.time()
        return session_id, result_text

    async def _fallback_query(
        self,
        prompt: str,
        resume: str | None,
        on_stream=None,
    ) -> tuple[str | None, str]:
        """连接失败时的 fallback：一次性 query。"""
        from claude_agent_sdk import query as sdk_query

        log_cc.info("CC fallback query: channel=%s", self.channel_id)
        options = self._build_options(resume=resume)

        session_id = None
        result_text = ""
        streaming_text = ""
        last_stream_time = 0.0

        async for message in sdk_query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        streaming_text += block.text
                        now = time.time()
                        if on_stream and now - last_stream_time > 2:
                            last_stream_time = now
                            try:
                                await on_stream(streaming_text)
                            except Exception:
                                pass

            elif isinstance(message, ResultMessage):
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
    on_stream=None,
) -> tuple[str | None, str]:
    """执行 CC 任务，复用或创建 channel 的持久会话。

    Args:
        on_stream: async callable(text: str)，CC 输出过程中定期调用（约每 2 秒）
    """
    cwd = Path(cwd)

    session = _sessions.get(channel_id)
    if session is None or str(session.cwd) != str(cwd):
        session = _LiveSession(channel_id, cwd, system_append)
        # 应用预设的 model override
        if channel_id in _model_overrides:
            session.model = _model_overrides[channel_id]
        _sessions[channel_id] = session
    else:
        session._system_append = system_append

    return await session.query(prompt, resume=resume, on_stream=on_stream)


def get_session_info(channel_id: str) -> dict:
    """获取 channel 的 CC 会话信息（供 /status /model /usage 命令使用）。"""
    session = _sessions.get(channel_id)
    # model 优先从 session 读，其次从 overrides 读
    model = None
    if session:
        model = session.model
    if not model and channel_id in _model_overrides:
        model = _model_overrides[channel_id]

    if not session:
        return {
            "connected": False,
            "session_id": None,
            "model": model or "default",
            "total_cost": 0,
            "total_queries": 0,
            "last_activity": 0,
        }
    return {
        "connected": session._connected,
        "session_id": session.session_id,
        "model": model or "default",
        "total_cost": round(session.total_cost, 4),
        "total_queries": session.total_queries,
        "last_activity": session.last_activity,
    }


async def set_model(channel_id: str, model: str | None) -> str:
    """切换 channel 的 CC 模型。"""
    # 始终存到 overrides（即使 session 还不存在）
    _model_overrides[channel_id] = model

    session = _sessions.get(channel_id)
    if session:
        session.model = model
        if session._connected:
            await session._disconnect()
            log_cc.info("CC 模型切换: %s, 已断开（下条消息重连生效）", model or "default")
    else:
        log_cc.info("CC 模型预设: %s（session 尚未创建）", model or "default")

    return model or "default"
