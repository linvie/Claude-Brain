"""Haiku LLM 封装 — 供 extractor 和 views 共用，通过 CC SDK 调用。

不直接依赖 anthropic 包和 ANTHROPIC_API_KEY，而是通过 claude_agent_sdk
启动轻量级 one-shot CC 进程，复用用户的 Claude 套餐 token。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk import (
    query as sdk_query,
)

from brain.config import MEMORY_EXTRACTION_MODEL
from brain.infra.logger import log_memory as log

# 重试配置
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # 秒

# CC SDK query 的工作目录（不需要实际项目，只要目录存在即可）
_CWD = Path.home() / ".ccbrain"


async def haiku_complete(
    system: str,
    user_message: str,
    *,
    max_tokens: int = 1024,
    model: str | None = None,
) -> str:
    """调用 Haiku（via CC SDK），返回文本结果。失败返回空字符串。

    自动重试错误，最多 _MAX_RETRIES 次。
    """
    model = model or MEMORY_EXTRACTION_MODEL

    for attempt in range(_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            text = await _query_once(system, user_message, model=model)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log.info("[llm] Haiku 完成: model=%s, %dms", model, elapsed_ms)
            return text
        except Exception:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if attempt < _MAX_RETRIES:
                log.warning(
                    "[llm] CC SDK 调用失败 (%dms), 重试 %d/%d",
                    elapsed_ms, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                log.exception("[llm] CC SDK 调用失败, 重试耗尽 (%dms)", elapsed_ms)
                return ""

    return ""


async def _query_once(
    system: str,
    user_message: str,
    *,
    model: str,
) -> str:
    """单次 CC SDK query，提取文本结果。"""
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system,
        cwd=str(_CWD),
        max_turns=1,
        permission_mode="bypassPermissions",
    )

    text = ""
    async for message in sdk_query(prompt=user_message, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text:
                    text += block.text
        elif isinstance(message, ResultMessage):
            # ResultMessage.result 是最终文本（如有），优先使用
            if message.result:
                text = message.result
    return text
