"""Haiku LLM 封装 — 供 extractor 和 views 共用的 Anthropic API 调用。"""

from __future__ import annotations

import asyncio
import os
import time

import anthropic

from brain.config import MEMORY_EXTRACTION_MODEL
from brain.infra.logger import log_memory as log

# 重试配置
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # 秒

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    """获取或创建 AsyncAnthropic 客户端（单例）。"""
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY 环境变量未设置")
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


async def haiku_complete(
    system: str,
    user_message: str,
    *,
    max_tokens: int = 1024,
    model: str | None = None,
) -> str:
    """调用 Haiku，返回文本结果。失败返回空字符串。

    自动重试 429（rate limit）和 5xx 错误，最多 _MAX_RETRIES 次。
    """
    model = model or MEMORY_EXTRACTION_MODEL
    client = _get_client()

    for attempt in range(_MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text

            usage = response.usage
            log.info(
                "[llm] Haiku 完成: model=%s, %dms, in=%d/out=%d tokens",
                model, elapsed_ms,
                usage.input_tokens, usage.output_tokens,
            )
            return text

        except anthropic.RateLimitError:
            if attempt < _MAX_RETRIES:
                log.warning("[llm] 429 rate limit, 重试 %d/%d", attempt + 1, _MAX_RETRIES)
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                log.error("[llm] 429 rate limit, 重试耗尽")
                return ""

        except anthropic.InternalServerError:
            if attempt < _MAX_RETRIES:
                log.warning("[llm] 5xx 服务器错误, 重试 %d/%d", attempt + 1, _MAX_RETRIES)
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                log.error("[llm] 5xx 服务器错误, 重试耗尽")
                return ""

        except anthropic.AuthenticationError:
            log.error("[llm] API key 无效")
            return ""

        except Exception:
            log.exception("[llm] Haiku 调用异常")
            return ""

    return ""
