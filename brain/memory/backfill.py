"""回填历史飞书 session 记忆。

一次性脚本：将记忆系统上线前的历史飞书 session JSONL 回填到记忆系统中。
可重复执行（已存在的 session_id 跳过）。

Usage:
    python -m brain.memory.backfill [--dry-run]
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from brain.config import DATA_DIR, MEMORY_LEDGER_DIR

# 加载 ~/.ccbrain/.env（如果存在）
load_dotenv(DATA_DIR / ".env")
from brain.infra.db import get_db
from brain.infra.logger import log_memory as log
from brain.memory.extractor import extract_from_session
from brain.memory.views import run_daily_views_job

# ── 常量 ──

CHANNEL_ID = "oc_9faed4a6f0604c04694e8bf97c1c18a9"

# 历史 JSONL 所在目录
SOURCE_DIR = Path.home() / ".claude/projects/-Users-linvie--ccbrain-workspaces-oc-9faed4a6f0604c04694e8bf97c1c18a9"

# 当前活跃 session，跳过
ACTIVE_SESSION_ID = "20c91a67-e727-487f-935d-a5afde9f99db"

# 最小对话轮数（低于此值跳过，与 extractor 一致）
_MIN_TURNS = 3


def _parse_timestamps(jsonl_path: Path) -> tuple[int | None, int | None]:
    """从 JSONL 提取首尾时间戳（ISO 8601 → unix epoch）。"""
    first_ts: int | None = None
    last_ts: int | None = None

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = entry.get("timestamp")
            if not ts_str:
                continue
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                epoch = int(dt.timestamp())
                if first_ts is None:
                    first_ts = epoch
                last_ts = epoch
            except (ValueError, AttributeError):
                continue

    return first_ts, last_ts


def _count_user_turns(jsonl_path: Path) -> int:
    """统计 JSONL 中用户消息轮数。

    SDK JSONL 格式：顶层 type="user" 表示用户消息。
    """
    count = 0
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "user":
                count += 1
    return count


def _convert_sdk_jsonl(src: Path, dest: Path):
    """将 SDK JSONL 格式转换为 extractor 期望的 role/content 格式。

    SDK 格式：顶层 type="user"/"assistant"，实际内容在 message 字段。
    Extractor 期望：顶层 role="user"/"assistant"，content 字段。
    """
    with open(src) as fin, open(dest, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            message = entry.get("message", {})
            if not isinstance(message, dict):
                continue

            # 提取 role 和 content
            role = message.get("role", entry_type)
            content = message.get("content", "")

            fout.write(json.dumps({"role": role, "content": content}, ensure_ascii=False) + "\n")


def discover_sessions(dry_run: bool = False) -> list[dict]:
    """发现并筛选符合条件的历史 JSONL 文件。"""
    if not SOURCE_DIR.exists():
        log.error("[backfill] 源目录不存在: %s", SOURCE_DIR)
        return []

    sessions = []
    for jsonl_path in sorted(SOURCE_DIR.glob("*.jsonl")):
        session_id = jsonl_path.stem

        # 排除活跃 session
        if session_id == ACTIVE_SESSION_ID:
            log.info("[backfill] 跳过活跃 session: %s", session_id)
            continue

        # 排除 subagents 目录
        if "subagents" in str(jsonl_path):
            continue

        # 检查文件大小
        if jsonl_path.stat().st_size == 0:
            log.info("[backfill] 跳过空文件: %s", session_id)
            continue

        # 统计对话轮数
        turns = _count_user_turns(jsonl_path)
        if turns < _MIN_TURNS:
            log.info("[backfill] 对话过短 (%d 轮), 跳过: %s", turns, session_id)
            continue

        # 解析时间范围
        opened_at, closed_at = _parse_timestamps(jsonl_path)
        if opened_at is None:
            log.warning("[backfill] 无法解析时间戳, 跳过: %s", session_id)
            continue

        sessions.append({
            "session_id": session_id,
            "jsonl_path": jsonl_path,
            "opened_at": opened_at,
            "closed_at": closed_at or opened_at,
            "turns": turns,
        })

    log.info("[backfill] 发现 %d 个符合条件的 session", len(sessions))
    return sessions


def _session_status(conn: sqlite3.Connection, session_id: str) -> str:
    """检查 session 状态：'done' / 'needs_extraction' / 'new'。"""
    row = conn.execute(
        "SELECT extracted_at FROM memory_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return "new"
    return "done" if row["extracted_at"] is not None else "needs_extraction"


async def backfill_one(
    conn: sqlite3.Connection,
    session: dict,
) -> int:
    """回填单个 session，返回提取的记忆数。"""
    session_id = session["session_id"]
    src_path = session["jsonl_path"]

    status = _session_status(conn, session_id)

    # 幂等：已完成提取则跳过
    if status == "done":
        log.info("[backfill] session 已完成, 跳过: %s", session_id)
        return -1  # sentinel: already done

    # 1. 转换并复制到 ledger（如果尚未存在）
    MEMORY_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = MEMORY_LEDGER_DIR / f"{session_id}.jsonl"
    if not ledger_path.exists():
        _convert_sdk_jsonl(src_path, ledger_path)
        log.info("[backfill] 归档 → %s", ledger_path)

    # 2. INSERT INTO memory_sessions（如果是新 session）
    if status == "new":
        conn.execute(
            """
            INSERT INTO memory_sessions (session_id, channel_id, opened_at, closed_at, jsonl_path, message_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                CHANNEL_ID,
                session["opened_at"],
                session["closed_at"],
                str(ledger_path),
                session["turns"],
            ),
        )
        conn.commit()

    # 3. 调用 Haiku 提取记忆
    count = await extract_from_session(
        conn=conn,
        session_id=session_id,
        jsonl_path=ledger_path,
        channel_id=CHANNEL_ID,
    )
    log.info("[backfill] session %s: 提取 %d 条记忆", session_id, count)
    return count


async def run_backfill(dry_run: bool = False):
    """主入口：回填全部历史 session。"""
    sessions = discover_sessions(dry_run=dry_run)
    if not sessions:
        print("没有发现需要回填的 session")
        return

    if dry_run:
        print(f"[DRY RUN] 发现 {len(sessions)} 个 session:")
        for s in sessions:
            dt = datetime.fromtimestamp(s["opened_at"], tz=timezone.utc)
            print(f"  {s['session_id']}  {dt:%Y-%m-%d %H:%M}  {s['turns']} turns")
        return

    conn = get_db()
    total_memories = 0
    processed = 0
    skipped = 0

    for i, session in enumerate(sessions, 1):
        print(f"[{i}/{len(sessions)}] {session['session_id']} ({session['turns']} turns)...")
        try:
            count = await backfill_one(conn, session)
            if count == -1:
                skipped += 1
            else:
                processed += 1
                total_memories += count
        except Exception:
            log.exception("[backfill] 处理失败: %s", session["session_id"])
            print("  ✗ 失败，跳过")
            continue

    print("\n回填完成:")
    print(f"  处理: {processed} sessions")
    print(f"  跳过: {skipped} sessions (已存在)")
    print(f"  提取: {total_memories} 条记忆")

    # 4. 生成历史日期的 daily views
    print("\n生成 daily views...")
    await run_daily_views_job(conn)

    # 最终统计
    row = conn.execute("SELECT COUNT(*) as cnt FROM memory_sessions").fetchone()
    print(f"  memory_sessions 总数: {row['cnt']}")
    row = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()
    print(f"  memories 总数: {row['cnt']}")

    conn.close()


def main():
    dry_run = "--dry-run" in sys.argv
    asyncio.run(run_backfill(dry_run=dry_run))


if __name__ == "__main__":
    main()
