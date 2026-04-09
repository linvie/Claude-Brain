"""Brain Daemon 主模块 — asyncio 主循环，v1 Notion 轮询 + v2 channel 并行。"""

import asyncio
import signal
import time

from brain.config import (
    ACTIVE_INTERVAL,
    COOLDOWN_DURATION,
    COOLDOWN_INTERVAL,
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


def _handle_signal(sig: signal.Signals):
    """信号处理：设置 shutdown event，通知所有 async task 退出。"""
    name = signal.Signals(sig).name
    log.info("收到 %s 信号，准备关闭", name)
    if _shutdown_event:
        _shutdown_event.set()


def _on_task_exception(task: asyncio.Task):
    """asyncio task 异常兜底：记录日志，避免异常静默丢失。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("async task [%s] 异常退出: %s", task.get_name(), exc, exc_info=exc)


async def _notion_poll_loop(conn, shutdown: asyncio.Event):
    """v1 Notion 轮询循环 — 从同步 while 迁移为 async task。"""
    cycle = 0
    last_task_done_time = 0.0

    while not shutdown.is_set():
        cycle += 1
        running_before = running_task_count(conn)

        # 1. 运行中任务：检查超时 + 收集结果
        if running_before > 0:
            watchdog(conn)
            check_all_outboxes(conn)
            check_tester_stops(conn)

        # 检测是否有任务刚完成
        running_after = running_task_count(conn)
        if running_after < running_before:
            last_task_done_time = time.time()
            log.info("[notion] 检测到任务完成，进入 cooldown 轮询模式")

        # 2. 有空槽位：查询并分发新任务
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

        # 3. 选择轮询间隔
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
            pass  # 正常：超时意味着继续下一轮


async def main():
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    # 信号处理
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

    # v2: Channel adapters 将在此处启动（Milestone 2）

    # 等待关闭信号
    await _shutdown_event.wait()
    log.info("正在关闭...")

    # 取消所有 task
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    conn.close()
    log.info("数据库连接已关闭，Brain Daemon 已退出")
