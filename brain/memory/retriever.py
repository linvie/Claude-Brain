"""记忆检索 — 从消息中提取关键词，检索相关记忆，组装 context。"""

from __future__ import annotations

import sqlite3

from brain.memory.store import search_memories


def build_memory_context(conn: sqlite3.Connection, message: str) -> str:
    """根据用户消息检索相关记忆，组装为可注入的 context 文本。

    返回空字符串表示无可用记忆。
    """
    # 用消息中的关键词检索
    memories = search_memories(conn, query=message, limit=10)

    if not memories:
        return ""

    lines = ["以下是你已知的信息（来自之前的对话）：", ""]
    for m in memories:
        lines.append(f"- [{m['type']}] {m['content']}")
    lines.append("")

    return "\n".join(lines)
