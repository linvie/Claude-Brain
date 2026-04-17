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

from brain.config import (
    MEMORY_ALWAYS_ON_THRESHOLD,
    MEMORY_ENABLED,
    SESSION_IDLE_TIMEOUT,
    SESSION_MAX_CONTEXT_TOKENS,
    SESSION_RESET_THRESHOLD,
    SESSION_WARM_THRESHOLD,
)
from brain.infra.logger import log_cc

# Cold reset 时注入 always-on 记忆的 token 预算（≈ 8000 字符）
_MEMORY_INJECT_CHAR_BUDGET = 8000

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
        self.session_id: str | None = None  # SDK session_id (from ResultMessage)
        self._db_session_id: str | None = None  # DB memory_sessions.session_id
        self.last_activity: float = 0
        self._system_append = system_append
        self._idle_task: asyncio.Task | None = None
        self._connected = False
        self.model: str | None = None  # 当前模型（用户可通过 /model 切换）
        self.total_cost: float = 0.0   # 累计费用
        self.total_queries: int = 0    # 累计查询数
        self.last_context_tokens: int = 0  # 上次 query 的 context token 数

    def _get_session_temperature(self) -> str:
        """根据距上次活动的时间间隔判断 session 温度。

        Returns:
            "hot"  — 间隔 < SESSION_WARM_THRESHOLD（缓存仍在 TTL 内）
            "warm" — SESSION_WARM_THRESHOLD ≤ 间隔 < SESSION_RESET_THRESHOLD
            "cold" — 间隔 ≥ SESSION_RESET_THRESHOLD
        """
        if self.last_activity <= 0:
            return "cold"
        idle = time.time() - self.last_activity
        if idle < SESSION_WARM_THRESHOLD:
            return "hot"
        if idle < SESSION_RESET_THRESHOLD:
            return "warm"
        return "cold"

    def _update_context_tokens(self, message: ResultMessage):
        """从 ResultMessage.usage 读取 input_tokens 更新 last_context_tokens。

        如果 SDK 不提供 usage 字段，用 JSONL 文件大小 * 0.3 估算。
        """
        usage = getattr(message, "usage", None)
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", 0)
            if input_tokens:
                self.last_context_tokens = int(input_tokens)
                return

        # 备选方案：JSONL 文件大小 * 0.3 估算
        estimated = self._estimate_tokens_from_jsonl()
        if estimated > 0:
            self.last_context_tokens = estimated
            log_cc.debug(
                "Token 估算（JSONL）: channel=%s, estimated=%d",
                self.channel_id, estimated,
            )

    def _estimate_tokens_from_jsonl(self) -> int:
        """从 SDK JSONL 文件大小估算 token 数（1 byte ≈ 0.3 token）。"""
        jsonl_path = self._find_sdk_jsonl()
        if jsonl_path and jsonl_path.exists():
            return int(jsonl_path.stat().st_size * 0.3)
        return 0

    async def _compact_session(self, on_stream=None) -> bool:
        """Warm 策略：通过 /compact 压缩 context，降低 cache write 成本。

        Returns:
            True 如果 compact 成功完成，False 如果失败（调用方应降级跳过）。
        """
        if not self.client or not self._connected:
            return False

        log_cc.info("Warm compact 开始: channel=%s", self.channel_id)

        if on_stream:
            try:
                await on_stream("🔄 正在整理上下文，请稍候…")
            except Exception:
                pass

        try:
            await self.client.query("/compact")
            # 消费 compact 的响应流
            async for message in self.client.receive_response():
                if isinstance(message, ResultMessage):
                    log_cc.info(
                        "Warm compact 完成: channel=%s, session=%s",
                        self.channel_id, getattr(message, "session_id", None),
                    )
                    break
            self.last_activity = time.time()
            return True
        except Exception:
            log_cc.warning("Warm compact 失败（降级跳过）: channel=%s", self.channel_id, exc_info=True)
            return False

    async def _reset_session(self, on_stream=None):
        """Cold 策略：断开旧 session，清除 session_id，准备创建全新 session。

        流程：
        1. 断开当前连接（触发 JSONL 归档 + 记忆提取）
        2. 清除 session_id 和 _db_session_id，使下次 _ensure_connected 创建全新 session
        """
        log_cc.info("Cold reset 开始: channel=%s, old_session=%s", self.channel_id, self.session_id)

        if on_stream:
            try:
                await on_stream("🔄 对话已久未活动，正在重置会话…")
            except Exception:
                pass

        # 断开当前连接（触发正常 disconnect 流程）
        await self._disconnect()

        # 清除 session 标识，使下次连接创建全新 session（不 resume）
        self.session_id = None
        self._db_session_id = None

        log_cc.info("Cold reset 完成: channel=%s", self.channel_id)

    def _build_memory_append(self) -> str:
        """查询 always-on 记忆（importance >= 阈值），格式化为 system_append 片段。

        Returns:
            格式化的记忆文本，token 预算不超过 ~2000 tokens（8000 字符）。
            无记忆时返回空字符串。
        """
        if not MEMORY_ENABLED:
            return ""

        try:
            from brain.infra.db import get_db

            conn = get_db()
            rows = conn.execute(
                """SELECT type, content, importance
                   FROM memories
                   WHERE importance >= ?
                   ORDER BY importance DESC, created_at DESC""",
                (MEMORY_ALWAYS_ON_THRESHOLD,),
            ).fetchall()

            if not rows:
                return ""

            lines = ["\n\n## 用户记忆（来自历史对话）\n"]
            char_count = len(lines[0])

            for row in rows:
                line = f"- [{row['type']}] {row['content']}"
                if char_count + len(line) + 1 > _MEMORY_INJECT_CHAR_BUDGET:
                    lines.append("...(记忆已截断)")
                    break
                lines.append(line)
                char_count += len(line) + 1  # +1 for newline

            result = "\n".join(lines)
            log_cc.info(
                "Cold reset 记忆注入: channel=%s, %d/%d 条记忆, %d 字符",
                self.channel_id, len(lines) - 1, len(rows), len(result),
            )
            return result
        except Exception:
            log_cc.warning("Cold reset 记忆查询失败（跳过注入）: channel=%s", self.channel_id, exc_info=True)
            return ""

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

        # Phase B: 记录 session 开始
        if MEMORY_ENABLED:
            self._record_session_open()

    async def _disconnect(self):
        """断开连接，释放 CC 进程。"""
        if self.client and self._connected:
            # 先断开 SDK 连接（确保 JSONL 已刷盘）
            try:
                await self.client.disconnect()
            except Exception:
                log_cc.debug("CC disconnect 异常（忽略）: channel=%s", self.channel_id)
            self._connected = False
            log_cc.info("CC 已断开: channel=%s, idle 超时", self.channel_id)

            # Phase B: 归档 JSONL + 更新 memory_sessions + 异步提取
            # 必须在 client.disconnect() 之后，SDK 才会完成 JSONL 写入
            if MEMORY_ENABLED and self.session_id:
                jsonl_path = self._record_session_close()
                if jsonl_path:
                    asyncio.create_task(self._async_extract(jsonl_path))
                else:
                    log_cc.warning(
                        "session close 未获取 JSONL 路径，跳过记忆提取: channel=%s, session=%s",
                        self.channel_id, self.session_id,
                    )

    async def _idle_watcher(self):
        """监视 idle 超时，自动断开连接。"""
        while self._connected:
            await asyncio.sleep(60)  # 每分钟检查一次
            if self._connected and time.time() - self.last_activity > SESSION_IDLE_TIMEOUT:
                await self._disconnect()
                return

    # ── Phase B: memory_sessions 追踪 ──

    def _record_session_open(self):
        """INSERT memory_sessions 记录 session 开始。"""
        try:
            from brain.infra.db import get_db
            conn = get_db()
            db_sid = f"{self.channel_id}:{int(time.time())}"
            conn.execute(
                "INSERT OR IGNORE INTO memory_sessions "
                "(session_id, channel_id, opened_at) VALUES (?, ?, ?)",
                (db_sid, self.channel_id, int(time.time())),
            )
            conn.commit()
            self._db_session_id = db_sid
        except Exception:
            log_cc.debug("记录 session open 失败（忽略）: channel=%s", self.channel_id)

    def _record_session_close(self) -> Path | None:
        """归档 JSONL + UPDATE memory_sessions。返回归档后的 JSONL 路径。"""
        try:
            from brain.infra.db import get_db
            from brain.memory.ledger import archive_session_jsonl

            sdk_jsonl = self._find_sdk_jsonl()
            jsonl_path: str | None = None
            archived_path: Path | None = None
            if sdk_jsonl:
                archived_path = archive_session_jsonl(self.session_id, sdk_jsonl)
                if archived_path:
                    jsonl_path = str(archived_path)

            conn = get_db()
            conn.execute(
                "UPDATE memory_sessions SET closed_at = ?, jsonl_path = ? "
                "WHERE session_id = ("
                "  SELECT session_id FROM memory_sessions "
                "  WHERE channel_id = ? AND closed_at IS NULL "
                "  ORDER BY opened_at DESC LIMIT 1"
                ")",
                (int(time.time()), jsonl_path, self.channel_id),
            )
            conn.commit()
            log_cc.info("session close 已记录: channel=%s, jsonl=%s",
                        self.channel_id, jsonl_path)
            return archived_path
        except Exception:
            log_cc.debug("记录 session close 失败（忽略）: channel=%s", self.channel_id)
            return None

    async def _async_extract(self, jsonl_path: Path):
        """后台异步提取记忆（不阻塞 disconnect 流程）。"""
        try:
            from brain.infra.db import get_db
            from brain.memory.extractor import extract_from_session

            conn = get_db()
            # 使用 DB session_id（channel_id:timestamp）以便正确标记 extracted_at
            db_sid = self._db_session_id or self.session_id or self.channel_id
            count = await extract_from_session(
                conn=conn,
                session_id=db_sid,
                jsonl_path=jsonl_path,
                channel_id=self.channel_id,
            )
            if count > 0:
                log_cc.info("异步记忆提取完成: channel=%s, %d 条", self.channel_id, count)
            else:
                log_cc.info("异步记忆提取完成: channel=%s, 无新记忆", self.channel_id)
        except Exception:
            log_cc.exception("异步记忆提取失败: channel=%s", self.channel_id)

    def _record_message_count(self):
        """message_count++ for the active memory_session."""
        try:
            from brain.infra.db import get_db
            conn = get_db()
            conn.execute(
                "UPDATE memory_sessions SET message_count = message_count + 1 "
                "WHERE session_id = ("
                "  SELECT session_id FROM memory_sessions "
                "  WHERE channel_id = ? AND closed_at IS NULL "
                "  ORDER BY opened_at DESC LIMIT 1"
                ")",
                (self.channel_id,),
            )
            conn.commit()
        except Exception:
            log_cc.debug("message_count++ 失败（忽略）: channel=%s", self.channel_id)

    def _find_sdk_jsonl(self) -> Path | None:
        """定位 SDK 存储的 session JSONL 文件。

        SDK 路径格式: ~/.claude/projects/{project_hash}/{session_id}.jsonl
        project_hash 是 cwd 绝对路径中所有非字母数字字符替换为 '-'。
        由于规则可能随 SDK 版本变化，使用 glob 按 session_id 匹配更可靠。
        """
        if not self.session_id:
            log_cc.debug("_find_sdk_jsonl: session_id 为空")
            return None
        projects_dir = Path.home() / ".claude" / "projects"
        matches = list(projects_dir.glob(f"*/{self.session_id}.jsonl"))
        if matches:
            log_cc.debug("_find_sdk_jsonl: 找到 %s", matches[0])
            return matches[0]
        log_cc.warning("_find_sdk_jsonl: JSONL 不存在: session_id=%s", self.session_id)
        return None

    async def query(
        self,
        prompt: str,
        resume: str | None = None,
        on_stream: asyncio.coroutines = None,
    ) -> tuple[str | None, str, dict]:
        """发送消息并收集结果，支持流式回调。

        返回 (session_id, result_text, metadata)。metadata 包含 duration_ms、model 等。

        错误处理：
        - Layer 1 自愈：ProcessTransport 错误（CC 进程死了）→ 自动重连 + 重试一次
        - Layer 2 僵死检测：receive_response 无消息超过 RESPONSE_TIMEOUT → 主动断开
        - Layer 3 错误文案：返回友好的错误提示给用户

        Args:
            on_stream: async callable(text: str)，CC 每产出一段文本时调用
        """
        try:
            return await self._query_once(prompt, resume=resume, on_stream=on_stream)
        except Exception as e:
            err_msg = str(e)
            if "ProcessTransport" in err_msg or "not ready" in err_msg:
                log_cc.warning("CC 进程已死，自动重连重试: channel=%s", self.channel_id)
                self._connected = False
                self.client = None
                resume_id = self.session_id or resume
                try:
                    return await self._query_once(prompt, resume=resume_id, on_stream=on_stream)
                except Exception:
                    log_cc.exception("CC 自愈重试仍失败: channel=%s", self.channel_id)
                    return None, "⚠️ 会话临时中断，已尝试恢复但失败。请 /reset 开新会话后重试。", {}
            if isinstance(e, asyncio.TimeoutError):
                log_cc.warning("CC 响应超时，断开重连: channel=%s", self.channel_id)
                await self._disconnect()
                return None, "⚠️ 上一次请求响应超时（可能因对话过长），已重置连接。请重新发送消息。", {}
            log_cc.exception("CC query 失败: channel=%s", self.channel_id)
            return None, f"⚠️ 处理消息时发生错误：{type(e).__name__}\n如果多次失败，请尝试 /reset 开新会话。", {}

    # receive_response 空闲超时（秒）：单次流式消息间隔超过此值则判定僵死
    _RESPONSE_IDLE_TIMEOUT = 180.0

    async def _query_once(
        self,
        prompt: str,
        resume: str | None = None,
        on_stream=None,
    ) -> tuple[str | None, str, dict]:
        """单次 query（不带重试）。返回 (session_id, result_text, metadata)。"""
        # 在连接前检测温度（基于上次活动时间）
        temperature = self._get_session_temperature()
        self.last_activity = time.time()

        try:
            await self._ensure_connected(resume=resume)
        except Exception:
            log_cc.exception("CC 连接失败: channel=%s", self.channel_id)
            return await self._fallback_query(prompt, resume, on_stream)

        # Context 安全网：token 超限时强制 compact（优先级高于温度策略）
        if (
            self.last_context_tokens > SESSION_MAX_CONTEXT_TOKENS
            and self._connected
        ):
            log_cc.info(
                "Context 超限，强制 compact: channel=%s, tokens=%d, max=%d",
                self.channel_id, self.last_context_tokens, SESSION_MAX_CONTEXT_TOKENS,
            )
            compacted = await self._compact_session(on_stream=on_stream)
            if compacted:
                self.last_context_tokens = 0  # compact 后重置，等下次 query 更新
            # 强制 compact 后跳过温度策略
        # Cold 策略：session 长时间未活动，reset + 记忆注入
        elif temperature == "cold" and self._connected:
            log_cc.info("Session 温度 cold，触发 reset: channel=%s", self.channel_id)
            await self._reset_session(on_stream=on_stream)
            # 注入 always-on 记忆到 system_append
            memory_append = self._build_memory_append()
            if memory_append:
                self._system_append += memory_append
            # reset 后需要重新连接（不 resume，创建全新 session）
            try:
                await self._ensure_connected(resume=None)
            except Exception:
                log_cc.exception("Cold reset 后重连失败: channel=%s", self.channel_id)
                return await self._fallback_query(prompt, resume, on_stream)

        # Warm 策略：缓存已过期，先 compact 压缩 context 再发用户消息
        elif temperature == "warm" and self._connected:
            log_cc.info("Session 温度 warm，触发 compact: channel=%s", self.channel_id)
            await self._compact_session(on_stream=on_stream)
            # compact 失败不阻塞，继续发送用户消息

        log_cc.info("CC query: channel=%s, connected=%s, prompt=%s",
                    self.channel_id, self._connected, prompt[:80])

        session_id = None
        result_text = ""
        streaming_text = ""
        last_stream_time = 0.0
        model_name = None
        metadata: dict = {}

        try:
            await self.client.query(prompt)
            response_iter = self.client.receive_response().__aiter__()
            while True:
                try:
                    message = await asyncio.wait_for(
                        response_iter.__anext__(),
                        timeout=self._RESPONSE_IDLE_TIMEOUT,
                    )
                except StopAsyncIteration:
                    break

                if isinstance(message, AssistantMessage):
                    model_name = getattr(message, "model", None) or model_name
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
                    cost = getattr(message, "total_cost_usd", 0) or 0
                    self.total_cost += cost
                    self.total_queries += 1
                    # Token 追踪：从 usage 读取 input_tokens
                    self._update_context_tokens(message)
                    metadata = {
                        "duration_ms": getattr(message, "duration_ms", 0) or 0,
                        "model": model_name,
                        "total_cost_usd": cost,
                        "num_turns": getattr(message, "num_turns", 0) or 0,
                    }
                    log_cc.info(
                        "CC 完成: session=%s, cost=$%.4f, ctx_tokens=%d, result=%s",
                        session_id, cost, self.last_context_tokens, result_text[:100],
                    )
        except Exception:
            log_cc.exception("CC query 异常: channel=%s", self.channel_id)
            self._connected = False
            raise

        self.session_id = session_id
        self.last_activity = time.time()

        # Phase B: message_count++
        if MEMORY_ENABLED and session_id:
            self._record_message_count()

        return session_id, result_text, metadata

    async def _fallback_query(
        self,
        prompt: str,
        resume: str | None,
        on_stream=None,
    ) -> tuple[str | None, str, dict]:
        """连接失败时的 fallback：一次性 query。"""
        from claude_agent_sdk import query as sdk_query

        log_cc.info("CC fallback query: channel=%s", self.channel_id)
        options = self._build_options(resume=resume)

        session_id = None
        result_text = ""
        streaming_text = ""
        last_stream_time = 0.0
        model_name = None
        metadata: dict = {}

        async for message in sdk_query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                model_name = getattr(message, "model", None) or model_name
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
                cost = getattr(message, "total_cost_usd", 0) or 0
                metadata = {
                    "duration_ms": getattr(message, "duration_ms", 0) or 0,
                    "model": model_name,
                    "total_cost_usd": cost,
                    "num_turns": getattr(message, "num_turns", 0) or 0,
                }

        self.session_id = session_id
        self.last_activity = time.time()
        return session_id, result_text, metadata


async def execute(
    *,
    prompt: str,
    cwd: str | Path,
    channel_id: str,
    system_append: str = "",
    resume: str | None = None,
    on_stream=None,
) -> tuple[str | None, str, dict]:
    """执行 CC 任务，复用或创建 channel 的持久会话。

    返回 (session_id, result_text, metadata)。metadata 含 duration_ms、model、total_cost_usd 等。

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


async def one_shot_query(
    prompt: str,
    cwd: str | Path,
    system_append: str = "",
    timeout: float = 120.0,
    model: str | None = None,
) -> str:
    """独立一次性 query：完全隔离的 CC 进程，不复用任何 session。

    用于 /doctor 等需要隔离环境的诊断/工具任务。即使当前 channel 的
    session 已死，这个调用也能正常工作。

    Args:
        prompt: 用户指令
        cwd: 工作目录（可与 channel workspace 相同）
        system_append: 追加到 system prompt 的指令（角色定位等）
        timeout: 总超时（秒），超时返回错误文案

    Returns:
        result_text 字符串。失败时返回错误说明而非抛异常。
    """
    cwd = Path(cwd)
    system_prompt: dict = {"type": "preset", "preset": "claude_code"}
    if system_append:
        system_prompt["append"] = system_append

    options = ClaudeAgentOptions(
        cwd=str(cwd),
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        model=model,
        setting_sources=["project", "user", "local"],
    )

    log_cc.info("one_shot_query 启动: cwd=%s, model=%s, prompt=%s", cwd, model, prompt[:80])

    try:
        return await asyncio.wait_for(
            _consume_one_shot(prompt, options),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log_cc.warning("one_shot_query 超时: cwd=%s", cwd)
        return f"⚠️ 任务超时（{int(timeout)}s 未完成）"
    except Exception as e:
        log_cc.exception("one_shot_query 失败: cwd=%s", cwd)
        return f"⚠️ 任务执行失败：{type(e).__name__}: {e}"


async def _consume_one_shot(prompt: str, options: ClaudeAgentOptions) -> str:
    """跑完一次 sdk_query，收集最终 result_text。"""
    from claude_agent_sdk import query as sdk_query

    result_text = ""
    async for message in sdk_query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result_text = message.result or ""
    return result_text


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
