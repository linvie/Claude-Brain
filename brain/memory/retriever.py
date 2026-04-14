"""Context Bridge — 三层检索 + 时间衰减，组装记忆 context 注入 system prompt。"""

from __future__ import annotations

import math
import sqlite3
import time

from brain.config import (
    MEMORY_ALWAYS_ON_THRESHOLD,
    MEMORY_DECAY_HALF_LIFE,
    MEMORY_MAX_CONTEXT_TOKENS,
)
from brain.infra.logger import log_memory as log


def decay_score(importance: int, age_days: float, half_life: float = MEMORY_DECAY_HALF_LIFE) -> float:
    """Ebbinghaus-inspired decay: importance * e^(-0.693 * age_days / half_life)."""
    if half_life <= 0:
        return float(importance)
    return importance * math.exp(-0.693 * age_days / half_life)


_PUNCTUATION = set("，。！？、；：""''（）()[]【】《》<>,.!?;:'\"·…—– \t\n")


def _is_cjk(char: str) -> bool:
    """判断字符是否为 CJK 字符。"""
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF       # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF    # CJK Extension A
        or 0xF900 <= cp <= 0xFAFF    # CJK Compatibility Ideographs
    )


def _fts5_query(message: str) -> str:
    """从用户消息中提取 FTS5 trigram 查询词。

    trigram tokenizer 要求每个 MATCH 项至少 3 个字符。
    中文无空格分隔，采用滑动窗口提取 3-gram 子串。
    英文按空格分词，保留 >= 3 字符的词。
    """
    tokens: list[str] = []

    # 1. 按空格拆分，分离中文段和英文词
    for word in message.split():
        cleaned = "".join(c for c in word if c not in _PUNCTUATION)
        if not cleaned:
            continue

        if any(_is_cjk(c) for c in cleaned):
            # 中文段：提取 3-gram 滑窗
            cjk_chars = [c for c in cleaned if _is_cjk(c)]
            for i in range(len(cjk_chars) - 2):
                gram = "".join(cjk_chars[i : i + 3])
                escaped = gram.replace('"', '""')
                tokens.append(f'"{escaped}"')
        else:
            # 英文词：至少 3 字符
            if len(cleaned) >= 3:
                escaped = cleaned.replace('"', '""')
                tokens.append(f'"{escaped}"')

    # 去重保持顺序
    seen: set[str] = set()
    unique = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    return " OR ".join(unique) if unique else ""


def build_memory_context(
    conn: sqlite3.Connection,
    message: str,
    channel_id: str | None = None,
    max_tokens: int = MEMORY_MAX_CONTEXT_TOKENS,
) -> str:
    """构建记忆上下文，返回格式化文本。

    三层检索策略：
    1. Always-on: importance >= threshold，始终注入
    2. Relevance: FTS5 MATCH 全文搜索
    3. Recent: 最近 7 天，scope 过滤

    结果去重、按 decay_score 排序、token 截断后格式化输出。
    """
    now = int(time.time())
    seen_ids: set[int] = set()
    scored: list[tuple[float, dict]] = []  # (score, memory_row)

    # ── 层 1: Always-on（高重要性）──
    always_on = conn.execute(
        """SELECT id, type, content, tags, importance, created_at, scope
           FROM memories
           WHERE importance >= ?
           ORDER BY importance DESC, created_at DESC
           LIMIT 5""",
        (MEMORY_ALWAYS_ON_THRESHOLD,),
    ).fetchall()

    for row in always_on:
        m = dict(row)
        seen_ids.add(m["id"])
        age_days = (now - m["created_at"]) / 86400
        score = decay_score(m["importance"], age_days)
        m["_layer"] = "always_on"
        scored.append((score, m))

    # ── 层 2: Relevance（FTS5 全文搜索）──
    fts_query = _fts5_query(message)
    if fts_query:
        try:
            relevance = conn.execute(
                """SELECT m.id, m.type, m.content, m.tags, m.importance, m.created_at, m.scope
                   FROM memories m
                   JOIN memories_fts ON memories_fts.rowid = m.id
                   WHERE memories_fts MATCH ?
                   ORDER BY rank
                   LIMIT 10""",
                (fts_query,),
            ).fetchall()

            for row in relevance:
                m = dict(row)
                if m["id"] in seen_ids:
                    continue
                seen_ids.add(m["id"])
                age_days = (now - m["created_at"]) / 86400
                score = decay_score(m["importance"], age_days)
                m["_layer"] = "relevance"
                scored.append((score, m))
        except sqlite3.OperationalError:
            # FTS5 表可能尚未创建或查询语法错误
            log.warning("[memory] FTS5 查询失败, query=%s", fts_query)

    # ── 层 3: Recent（最近 7 天）──
    seven_days_ago = now - 7 * 86400
    if channel_id:
        recent = conn.execute(
            """SELECT id, type, content, tags, importance, created_at, scope
               FROM memories
               WHERE created_at > ?
               AND (scope = 'global' OR scope = ?)
               ORDER BY created_at DESC
               LIMIT 5""",
            (seven_days_ago, f"channel:{channel_id}"),
        ).fetchall()
    else:
        recent = conn.execute(
            """SELECT id, type, content, tags, importance, created_at, scope
               FROM memories
               WHERE created_at > ?
               ORDER BY created_at DESC
               LIMIT 5""",
            (seven_days_ago,),
        ).fetchall()

    for row in recent:
        m = dict(row)
        if m["id"] in seen_ids:
            continue
        seen_ids.add(m["id"])
        age_days = (now - m["created_at"]) / 86400
        score = decay_score(m["importance"], age_days)
        m["_layer"] = "recent"
        scored.append((score, m))

    if not scored:
        return ""

    # ── 更新 last_accessed ──
    ids = [m["id"] for _, m in scored]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE memories SET last_accessed = ? WHERE id IN ({placeholders})",
        [now, *ids],
    )
    conn.commit()

    # ── 按 layer 分组 + 按 score 排序 ──
    always_on_items = sorted(
        [(s, m) for s, m in scored if m["_layer"] == "always_on"],
        key=lambda x: x[0], reverse=True,
    )
    relevance_items = sorted(
        [(s, m) for s, m in scored if m["_layer"] == "relevance"],
        key=lambda x: x[0], reverse=True,
    )
    recent_items = sorted(
        [(s, m) for s, m in scored if m["_layer"] == "recent"],
        key=lambda x: x[0], reverse=True,
    )

    # ── 格式化输出 + token 截断 ──
    lines: list[str] = ["以下是你已知的相关信息：", ""]

    # 粗略 token 估算：中文约 1 字 ≈ 1.5 token，英文约 1 词 ≈ 1.3 token
    # 简单用字符数 / 2 近似
    char_budget = max_tokens * 2

    def _format_item(score: float, m: dict) -> str:
        imp = m["importance"]
        return f"- [{m['type']}] {m['content']} (重要度: {imp}, 衰减分: {score:.1f})"

    if always_on_items:
        lines.append("## 重要信息")
        for score, m in always_on_items:
            line = _format_item(score, m)
            lines.append(line)
        lines.append("")

    if relevance_items:
        lines.append("## 相关记忆")
        for score, m in relevance_items:
            line = _format_item(score, m)
            lines.append(line)
        lines.append("")

    if recent_items:
        lines.append("## 最近对话")
        for score, m in recent_items:
            line = _format_item(score, m)
            lines.append(line)
        lines.append("")

    result = "\n".join(lines)

    # Token 截断
    if len(result) > char_budget:
        result = result[:char_budget] + "\n...(记忆已截断)"
        log.info("[memory] context 已截断至 %d 字符 (≈%d tokens)", char_budget, max_tokens)

    log.debug(
        "[memory] context: %d 条记忆 (always=%d, relevance=%d, recent=%d)",
        len(scored), len(always_on_items), len(relevance_items), len(recent_items),
    )
    return result
