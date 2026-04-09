"""记忆提取 — 从 CC 输出中提取值得记住的信息。"""

from __future__ import annotations

import re
import sqlite3

from brain.infra.logger import log_memory as log
from brain.memory.store import add_memory

# 匹配常见的"事实陈述"模式
_FACT_PATTERNS = [
    # "我/用户 + 动词" 模式（偏好、计划、信息）
    re.compile(r"(?:用户|你)(?:说|提到|表示|希望|喜欢|不喜欢|计划|打算).{5,80}"),
]


def extract_and_store(
    conn: sqlite3.Connection,
    cc_output: str,
    source: str,
) -> int:
    """从 CC 输出中提取记忆并存储。返回提取的记忆数量。

    Phase A 实现：简单规则匹配。后续可升级为 LLM 提取。
    """
    if not cc_output or len(cc_output) < 20:
        return 0

    count = 0
    for pattern in _FACT_PATTERNS:
        for match in pattern.finditer(cc_output):
            text = match.group(0).strip()
            if len(text) > 10:
                add_memory(
                    conn,
                    type="fact",
                    content=text,
                    source=source,
                    tags=["auto-extracted"],
                    importance=3,
                )
                count += 1

    if count > 0:
        log.info("[memory] 从 CC 输出提取 %d 条记忆", count)
    return count
