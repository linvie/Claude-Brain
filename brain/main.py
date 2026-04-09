"""Brain Daemon 主模块 — asyncio 主循环，v1 Notion 轮询 + v2 channel 并行。"""

import asyncio
import signal
import time

from brain.config import (
    ACTIVE_INTERVAL,
    COOLDOWN_DURATION,
    COOLDOWN_INTERVAL,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_ENABLED,
    IDLE_INTERVAL,
    MAX_CONCURRENT,
    MAX_TASK_DURATION,
    NOTION_ENABLED,
    WORKSPACE_BASE,
)
from brain.core.dispatcher import dispatch
from brain.core.outbox import check_all_outboxes
from brain.core.tester import check_tester_stops
from brain.core.watchdog import watchdog
from brain.infra.db import get_db, running_task_count
from brain.infra.logger import log, log_scheduler
from brain.integrations.notion import fetch_ready_tasks

_shutdown_event: asyncio.Event | None = None


# ---------------------------------------------------------------------------
# 信号处理 & task 异常兜底
# ---------------------------------------------------------------------------

def _handle_signal(sig: signal.Signals):
    name = signal.Signals(sig).name
    log.info("收到 %s 信号，准备关闭", name)
    if _shutdown_event:
        _shutdown_event.set()


def _on_task_exception(task: asyncio.Task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("async task [%s] 异常退出: %s", task.get_name(), exc, exc_info=exc)


# ---------------------------------------------------------------------------
# v1: Notion 轮询
# ---------------------------------------------------------------------------

async def _notion_poll_loop(conn, shutdown: asyncio.Event):
    cycle = 0
    last_task_done_time = 0.0

    while not shutdown.is_set():
        cycle += 1
        running_before = running_task_count(conn)

        if running_before > 0:
            watchdog(conn)
            check_all_outboxes(conn)
            check_tester_stops(conn)

        running_after = running_task_count(conn)
        if running_after < running_before:
            last_task_done_time = time.time()
            log.info("[notion] 检测到任务完成，进入 cooldown 轮询模式")

        if running_after < MAX_CONCURRENT:
            ready_tasks = fetch_ready_tasks()
            if ready_tasks:
                log_scheduler.info(
                    "发现 %d 个 Ready 任务, 当前运行 %d/%d",
                    len(ready_tasks), running_after, MAX_CONCURRENT,
                )
            for task in ready_tasks:
                if running_task_count(conn) >= MAX_CONCURRENT:
                    log_scheduler.info("已达并发上限 %d，剩余任务下轮处理", MAX_CONCURRENT)
                    break
                dispatch(conn, task)

        running_now = running_task_count(conn)
        if running_now > 0:
            interval = ACTIVE_INTERVAL
        elif time.time() - last_task_done_time < COOLDOWN_DURATION:
            interval = COOLDOWN_INTERVAL
        else:
            interval = IDLE_INTERVAL

        log.debug("[notion] cycle=%d, running=%d, sleep=%ds", cycle, running_now, interval)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# v2: 消息处理（飞书 → CC → 回复）
# ---------------------------------------------------------------------------

async def _handle_channel_message(incoming, adapter, conn):
    """处理来自 channel 的消息：发占位 → 执行 CC → 编辑结果。"""
    from brain.channels.base import OutgoingMessage
    from brain.executor.cc import execute
    from brain.memory.extractor import extract_and_store
    from brain.memory.retriever import build_memory_context
    from brain.session.manager import get_active_session, save_session, touch_session

    channel_id = incoming.channel_id

    # 1. 发送"思考中..."占位消息
    try:
        placeholder_msg = OutgoingMessage(
            channel_id=channel_id,
            text="思考中...",
            reply_to=incoming.message_id,
        )
        placeholder_id = await adapter.send(placeholder_msg)
    except Exception:
        log.exception("[handler] 发送占位消息失败")
        return

    try:
        # 2. 查找或创建 session
        session_id = get_active_session(conn, channel_id)
        if session_id:
            touch_session(conn, channel_id, session_id)
            log.info("[handler] 复用 session: channel=%s, session=%s", channel_id, session_id)

        # 3. 组装记忆 context
        memory_context = build_memory_context(conn, incoming.text)

        # 4. 执行 CC
        workspace = WORKSPACE_BASE / "v2-default"
        workspace.mkdir(parents=True, exist_ok=True)

        new_session_id, result_text = await execute(
            prompt=incoming.text,
            cwd=workspace,
            system_append=memory_context,
            resume=session_id,
        )

        # 5. 保存 session
        if new_session_id:
            save_session(conn, channel_id, new_session_id)

        # 6. 编辑占位消息为结果
        if result_text:
            await adapter.edit(placeholder_id, result_text)
        else:
            await adapter.edit(placeholder_id, "（CC 未返回结果）")

        # 7. 提取记忆
        if result_text:
            extract_and_store(conn, result_text, source=f"feishu:{channel_id}")

    except Exception:
        log.exception("[handler] 处理消息异常: channel=%s", channel_id)
        try:
            await adapter.edit(placeholder_id, "处理消息时发生错误，请稍后重试。")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

async def main():
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    log.info("Brain Daemon 启动")
    log.info(
        "配置: idle=%ds, active=%ds, cooldown=%ds/%ds, timeout=%ds, max_concurrent=%d",
        IDLE_INTERVAL, ACTIVE_INTERVAL, COOLDOWN_INTERVAL, COOLDOWN_DURATION,
        MAX_TASK_DURATION, MAX_CONCURRENT,
    )
    log.info("Workspace 根目录: %s", WORKSPACE_BASE)

    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    conn = get_db()

    tasks: list[asyncio.Task] = []

    # v1: Notion 轮询
    if NOTION_ENABLED:
        log.info("Notion 轮询已启用")
        t = asyncio.create_task(
            _notion_poll_loop(conn, _shutdown_event),
            name="notion-poll",
        )
        t.add_done_callback(_on_task_exception)
        tasks.append(t)
    else:
        log.info("Notion 轮询未启用（token 未配置）")

    # v2: 飞书 adapter
    feishu_adapter = None
    if FEISHU_ENABLED:
        from brain.channels.feishu.adapter import FeishuAdapter

        log.info("飞书 adapter 已启用")
        feishu_adapter = FeishuAdapter(FEISHU_APP_ID, FEISHU_APP_SECRET)
        feishu_adapter.on_message(
            lambda msg: _handle_channel_message(msg, feishu_adapter, conn)
        )
        t = asyncio.create_task(feishu_adapter.start(), name="feishu-ws")
        t.add_done_callback(_on_task_exception)
        tasks.append(t)
    else:
        log.info("飞书 adapter 未启用")

    if not tasks:
        log.warning("无任何事件源启用，Brain 将空转等待信号")

    # 等待关闭信号
    await _shutdown_event.wait()
    log.info("正在关闭...")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    conn.close()
    log.info("数据库连接已关闭，Brain Daemon 已退出")
