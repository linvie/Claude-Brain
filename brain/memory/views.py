"""Daily Views — 每日摘要生成器，合并碎片记忆为结构化 markdown。

Phase B Task 4/5：查询已关闭未摘要的 session，调用 Haiku 生成每日视图，
写入 ~/.ccbrain/memory/views/{YYYY-MM-DD}.md。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from brain.config import MEMORY_VIEWS_DIR
from brain.infra.logger import log_memory as log
from brain.memory._llm import haiku_complete

_VIEWS_SYSTEM = """\
你是一个记忆整理助手。根据提供的对话 session 信息，生成当日结构化摘要。

输出格式（严格遵守，不要添加额外标题或修改格式）：

## Sessions
- [channel_id] HH:MM-HH:MM: 一句话摘要

## Key Facts Learned
- 每条一行

## Decisions Made
- 每条一行

## Open Questions
- 每条一行

规则：
- 每个 section 如果没有内容，写 "（无）"
- 摘要语言与对话语言一致（中文对话用中文摘要）
- Sessions 按时间排序
- Key Facts / Decisions / Open Questions 去重、合并相似项
- 控制在 500 字以内"""


async def generate_daily_view(
    conn: sqlite3.Connection,
    date: str | None = None,
) -> Path | None:
    """生成指定日期的摘要视图，返回文件路径。无新内容返回 None。

    Args:
        conn: SQLite 连接
        date: 日期字符串 YYYY-MM-DD，默认今天
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 查询当日已关闭但未摘要的 session
    sessions = conn.execute(
        """
        SELECT session_id, channel_id, opened_at, closed_at, jsonl_path
        FROM memory_sessions
        WHERE summarized_at IS NULL
          AND closed_at IS NOT NULL
          AND date(closed_at, 'unixepoch') = ?
        ORDER BY opened_at ASC
        """,
        (date,),
    ).fetchall()

    if not sessions:
        log.debug("[views] 日期 %s 无未摘要 session", date)
        return None

    log.info("[views] 日期 %s: 发现 %d 个未摘要 session", date, len(sessions))

    # 收集各 session 的记忆和元数据
    session_summaries = _collect_session_info(conn, sessions)

    if not session_summaries.strip():
        log.info("[views] 日期 %s: 无有效内容可生成摘要", date)
        _mark_sessions_summarized(conn, sessions)
        return None

    # 调用 Haiku 生成摘要
    user_msg = f"以下是 {date} 的对话 session 信息：\n\n{session_summaries}"
    raw_output = await haiku_complete(
        system=_VIEWS_SYSTEM,
        user_message=user_msg,
        max_tokens=1024,
    )

    if not raw_output.strip():
        log.warning("[views] Haiku 无输出: date=%s", date)
        return None

    # 写入 markdown 文件
    view_path = _write_view_file(date, raw_output)

    # 标记 session 已摘要
    _mark_sessions_summarized(conn, sessions)

    log.info("[views] 生成 daily view: %s (%d sessions)", view_path, len(sessions))
    return view_path


async def run_daily_views_job(conn: sqlite3.Connection):
    """扫描所有未摘要的已关闭 session，按日期分组生成视图。

    由 main.py 定时调用。
    """
    # 查询所有未摘要的日期
    dates = conn.execute(
        """
        SELECT DISTINCT date(closed_at, 'unixepoch') as dt
        FROM memory_sessions
        WHERE summarized_at IS NULL
          AND closed_at IS NOT NULL
        ORDER BY dt ASC
        """,
    ).fetchall()

    if not dates:
        log.debug("[views] 无待生成的 daily view")
        return

    generated = 0
    for row in dates:
        dt = row["dt"]
        if dt is None:  # pragma: no cover — defensive; WHERE closed_at IS NOT NULL
            continue
        try:
            result = await generate_daily_view(conn, date=dt)
            if result:
                generated += 1
        except Exception:
            log.exception("[views] 生成 daily view 失败: date=%s", dt)

    if generated:
        log.info("[views] daily views job 完成: 生成 %d 个视图", generated)


def _collect_session_info(
    conn: sqlite3.Connection,
    sessions: list[sqlite3.Row],
) -> str:
    """收集 session 元数据和已提取的记忆，拼接为文本供 Haiku 生成摘要。"""
    parts: list[str] = []

    for sess in sessions:
        session_id = sess["session_id"]
        channel_id = sess["channel_id"]
        opened_at = sess["opened_at"]
        closed_at = sess["closed_at"]

        open_time = datetime.fromtimestamp(opened_at, tz=timezone.utc).strftime("%H:%M")
        close_time = datetime.fromtimestamp(closed_at, tz=timezone.utc).strftime("%H:%M")

        part = f"### Session: [{channel_id}] {open_time}-{close_time}\n"

        # 从 memories 表获取该 session 提取的记忆
        memories = conn.execute(
            """
            SELECT type, content, importance
            FROM memories
            WHERE source = ?
            ORDER BY importance DESC
            LIMIT 20
            """,
            (f"session:{session_id}",),
        ).fetchall()

        if memories:
            part += "已提取的记忆：\n"
            for mem in memories:
                part += f"- [{mem['type']}] (重要度:{mem['importance']}) {mem['content']}\n"
        else:
            part += "（此 session 无已提取记忆）\n"

        parts.append(part)

    return "\n".join(parts)


def _write_view_file(date: str, content: str) -> Path:
    """写入 daily view markdown 文件。"""
    MEMORY_VIEWS_DIR.mkdir(parents=True, exist_ok=True)
    view_path = MEMORY_VIEWS_DIR / f"{date}.md"

    header = f"# Daily Memory View — {date}\n\n"
    view_path.write_text(header + content, encoding="utf-8")
    return view_path


def _mark_sessions_summarized(
    conn: sqlite3.Connection,
    sessions: list[sqlite3.Row],
):
    """批量标记 session 为已摘要。"""
    now = int(time.time())
    session_ids = [s["session_id"] for s in sessions]
    if not session_ids:
        return
    placeholders = ",".join("?" for _ in session_ids)
    try:
        conn.execute(
            f"UPDATE memory_sessions SET summarized_at = ? "
            f"WHERE session_id IN ({placeholders})",
            [now, *session_ids],
        )
        conn.commit()
    except Exception:
        log.exception("[views] 批量更新 summarized_at 失败")
