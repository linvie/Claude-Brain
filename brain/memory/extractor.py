"""记忆提取 — 从 session JSONL 用 Haiku LLM 提取结构化记忆。

Phase B 实现：替代 Phase A 的正则匹配，用 Haiku 提取高质量记忆。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from brain.infra.logger import log_memory as log
from brain.memory._llm import haiku_complete
from brain.memory.store import add_memory

# 提取 prompt
_EXTRACTION_SYSTEM = """\
你是一个记忆提取助手。从对话中提取值得长期记住的信息。

规则：
- 每条记忆一行，格式：TYPE|IMPORTANCE|CONTENT
- TYPE 取值：fact / preference / decision / context
- IMPORTANCE: 1-10（10 最重要）
- CONTENT: 简洁中文描述（一句话）
- 只提取长期有价值的信息，忽略临时性操作细节（如"运行了某命令""创建了某文件"）
- 偏好和决策通常比事实更重要
- 如果对话无值得记住的内容，输出空行即可

示例输出：
preference|8|用户偏好用 SQLite 而非 PostgreSQL 作为本地存储
decision|7|选择 FTS5 全文搜索替代向量数据库方案
fact|6|项目使用 Python 3.12 + asyncio 架构"""

# 最小对话轮数（低于此值不提取）
_MIN_TURNS = 3
# 对话摘要截断长度（字符数，约 4000 tokens）
_MAX_CONVERSATION_CHARS = 12000

# TYPE|IMPORTANCE|CONTENT 格式解析
_LINE_PATTERN = re.compile(
    r"^(fact|preference|decision|context)\|(\d{1,2})\|(.+)$",
    re.IGNORECASE,
)


async def extract_from_session(
    conn: sqlite3.Connection,
    session_id: str,
    jsonl_path: Path,
    channel_id: str,
) -> int:
    """从 JSONL 提取记忆，返回提取数量。

    流程：
    1. 读取 JSONL，提取用户/助手文本
    2. 如果对话过短（< _MIN_TURNS 轮），跳过
    3. 拼接对话摘要（截断到 ~4000 tokens）
    4. 调用 Haiku 提取 TYPE|IMPORTANCE|CONTENT
    5. 写入 memories 表
    6. UPDATE memory_sessions SET extracted_at
    """
    if not jsonl_path or not jsonl_path.exists():
        log.warning("[extractor] JSONL 不存在: %s", jsonl_path)
        return 0

    # 1. 读取并解析 JSONL
    conversation = _parse_jsonl(jsonl_path)
    if not conversation:
        log.info("[extractor] JSONL 为空或无有效对话: %s", jsonl_path)
        return 0

    # 2. 检查对话轮数
    turn_count = sum(1 for role, _ in conversation if role == "user")
    if turn_count < _MIN_TURNS:
        log.info("[extractor] 对话过短 (%d 轮), 跳过提取: session=%s", turn_count, session_id)
        _mark_extracted(conn, session_id)
        return 0

    # 3. 拼接对话摘要
    summary = _build_conversation_summary(conversation)

    # 4. 调用 Haiku
    log.info("[extractor] 开始提取: session=%s, %d 轮对话", session_id, turn_count)
    raw_output = await haiku_complete(
        system=_EXTRACTION_SYSTEM,
        user_message=f"以下是对话内容：\n\n{summary}",
    )

    if not raw_output.strip():
        log.info("[extractor] Haiku 无输出: session=%s", session_id)
        _mark_extracted(conn, session_id)
        return 0

    # 5. 解析并写入
    count = _parse_and_store(conn, raw_output, session_id, channel_id)

    # 6. 标记已摘要
    _mark_extracted(conn, session_id)

    log.info("[extractor] 提取完成: session=%s, %d 条记忆", session_id, count)
    return count


def _parse_jsonl(jsonl_path: Path) -> list[tuple[str, str]]:
    """解析 JSONL 文件，返回 [(role, text), ...] 列表。"""
    conversation: list[tuple[str, str]] = []
    try:
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                role = entry.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                text = _extract_text_from_entry(entry)
                if text:
                    conversation.append((role, text))
    except Exception:
        log.exception("[extractor] JSONL 读取失败: %s", jsonl_path)
    return conversation


def _extract_text_from_entry(entry: dict) -> str:
    """从 JSONL entry 提取纯文本内容。"""
    content = entry.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts).strip()
    return ""


def _build_conversation_summary(conversation: list[tuple[str, str]]) -> str:
    """将对话拼接为摘要文本，超长时保留首尾。"""
    lines = []
    for role, text in conversation:
        prefix = "用户" if role == "user" else "助手"
        lines.append(f"{prefix}: {text}")

    full_text = "\n\n".join(lines)

    if len(full_text) <= _MAX_CONVERSATION_CHARS:
        return full_text

    # 保留前 60% + 后 40%
    head_len = int(_MAX_CONVERSATION_CHARS * 0.6)
    tail_len = _MAX_CONVERSATION_CHARS - head_len - 20  # 留 gap 标记
    return full_text[:head_len] + "\n\n[...对话中间省略...]\n\n" + full_text[-tail_len:]


def _parse_and_store(
    conn: sqlite3.Connection,
    raw_output: str,
    session_id: str,
    channel_id: str,
) -> int:
    """解析 Haiku 输出的 TYPE|IMPORTANCE|CONTENT 格式，写入 memories 表。"""
    count = 0
    for line in raw_output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_PATTERN.match(line)
        if not m:
            continue

        mem_type = m.group(1).lower()
        importance = min(max(int(m.group(2)), 1), 10)
        content = m.group(3).strip()

        if len(content) < 5:
            continue

        scope = f"channel:{channel_id}" if channel_id else "global"
        add_memory(
            conn,
            type=mem_type,
            content=content,
            source=f"session:{session_id}",
            tags=["llm-extracted"],
            importance=importance,
            scope=scope,
        )
        count += 1

    return count


def _mark_extracted(conn: sqlite3.Connection, session_id: str):
    """UPDATE memory_sessions SET extracted_at。

    session_id 来自 cc.py 的 _db_session_id（channel_id:timestamp 格式），
    直接精确匹配。
    """
    try:
        conn.execute(
            "UPDATE memory_sessions SET extracted_at = ? "
            "WHERE session_id = ?",
            (int(time.time()), session_id),
        )
        conn.commit()
    except Exception:
        log.debug("[extractor] 更新 extracted_at 失败: session=%s", session_id)


# ── Phase A 兼容接口 ──


def extract_and_store(
    conn: sqlite3.Connection,
    cc_output: str,
    source: str,
) -> int:
    """Phase A 兼容接口：简单正则提取。保留用于过渡期。

    新代码应使用 extract_from_session()。
    """
    if not cc_output or len(cc_output) < 20:
        return 0

    _FACT_PATTERNS = [
        re.compile(r"(?:用户|你)(?:说|提到|表示|希望|喜欢|不喜欢|计划|打算).{5,80}"),
    ]

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
