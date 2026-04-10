"""Brain Daemon 主模块 — asyncio 主循环，v1 Notion 轮询 + v2 channel 并行。"""

import asyncio
import signal
import time

from brain.config import (
    ACTIVE_INTERVAL,
    COOLDOWN_DURATION,
    COOLDOWN_INTERVAL,
    FEISHU_ALLOWED_USERS,
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
from brain.infra.logger import log, log_feishu, log_scheduler
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
# v2: Per-channel 队列 + 命令路由
# ---------------------------------------------------------------------------

# Brain 直接处理的命令（不需要 CC，立即响应）
_INSTANT_COMMANDS = {"/reset", "/status", "/help"}
# 需要 CC 但不阻塞队列的命令
_BACKGROUND_COMMANDS = {"/btw"}
# /btw 并发限制
_BTW_SEMAPHORE = asyncio.Semaphore(3)

# channel_id → asyncio.Queue，只有普通对话消息才入队
_channel_queues: dict[str, asyncio.Queue] = {}
_channel_workers: dict[str, asyncio.Task] = {}


async def _dispatch_message(incoming, adapter, conn):
    """消息分流：命令立即处理，对话排队。"""
    text = incoming.text.strip()
    cmd = text.split(None, 1)[0].lower() if text else ""

    if cmd in _INSTANT_COMMANDS:
        # 立即响应，不入队
        await _handle_command(incoming, adapter, conn)
    elif cmd in _BACKGROUND_COMMANDS:
        # 立即响应 + 后台 CC，不入队
        await _handle_command(incoming, adapter, conn)
    else:
        # 普通对话：入 per-channel 队列，线性处理
        await _enqueue_chat(incoming, adapter, conn)


async def _enqueue_chat(incoming, adapter, conn):
    """将对话消息放入 per-channel 队列。"""
    cid = incoming.channel_id
    if cid not in _channel_queues:
        _channel_queues[cid] = asyncio.Queue()
        worker = asyncio.create_task(
            _channel_worker(cid, adapter, conn),
            name=f"channel-{cid[:8]}",
        )
        worker.add_done_callback(_on_task_exception)
        _channel_workers[cid] = worker
    await _channel_queues[cid].put(incoming)


async def _channel_worker(channel_id: str, adapter, conn):
    """Per-channel worker：线性消费队列中的对话消息。"""
    queue = _channel_queues[channel_id]
    while True:
        incoming = await queue.get()
        try:
            await _handle_chat(incoming, adapter, conn)
        except Exception:
            log_feishu.exception("worker 异常: channel=%s", channel_id)
        finally:
            queue.task_done()


# ---------------------------------------------------------------------------
# 命令处理
# ---------------------------------------------------------------------------

async def _handle_command(incoming, adapter, conn):
    """解析并处理 /xxx 命令。"""
    from brain.channels.base import OutgoingMessage

    text = incoming.text.strip()
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/btw":
        # 后台任务：不阻塞队列，立即回复，CC 在后台执行
        if not arg:
            await adapter.send(OutgoingMessage(
                channel_id=incoming.channel_id,
                text="用法: /btw <任务描述>",
                reply_to=incoming.message_id,
            ))
            return
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text=f"已提交后台任务: {arg[:50]}",
            reply_to=incoming.message_id,
        ))
        asyncio.create_task(
            _run_background_task(incoming, adapter, conn, arg),
            name=f"btw-{incoming.channel_id[:8]}",
        )

    elif cmd == "/reset":
        # 重置 session：归档当前 session，下条消息开新 session
        from brain.session.manager import get_active_session
        session_id = get_active_session(conn, incoming.channel_id)
        if session_id:
            from brain.session.manager import _archive_session
            _archive_session(conn, incoming.channel_id, session_id)
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text="已重置对话，下条消息将开始新 session。",
            reply_to=incoming.message_id,
        ))

    elif cmd == "/status":
        from brain.session.manager import get_active_session
        session_id = get_active_session(conn, incoming.channel_id)
        status = f"Session: {session_id or '无'}"
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text=status,
            reply_to=incoming.message_id,
        ))

    elif cmd == "/help":
        help_text = (
            "**可用命令：**\n\n"
            "- `/btw <任务>` — 后台执行任务（不阻塞对话）\n"
            "- `/reset` — 重置对话 session\n"
            "- `/status` — 查看当前 session 状态\n"
            "- `/help` — 显示此帮助\n\n"
            "直接发消息即对话（线性处理，排队执行）。"
        )
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text=help_text,
            reply_to=incoming.message_id,
        ))

    # 不会走到这里，因为只有 _KNOWN_COMMANDS 才进入 _handle_command


async def _run_background_task(incoming, adapter, conn, task_desc: str):
    """后台执行 CC 任务，完成后回复。最多 3 个并发，超出排队等待。"""
    from brain.channels.base import OutgoingMessage
    from brain.executor.cc import execute
    from brain.session.manager import get_workspace

    channel_id = incoming.channel_id

    async with _BTW_SEMAPHORE:
        workspace = get_workspace(channel_id)
        try:
            _, result_text = await execute(
                prompt=task_desc,
                cwd=workspace,
                channel_id=channel_id,
            )
            reply = result_text if result_text else "（后台任务完成，无输出）"
            await adapter.send(OutgoingMessage(
                channel_id=channel_id,
                text=f"**后台任务完成：** {task_desc[:50]}\n\n{reply}",
                reply_to=incoming.message_id,
            ))
        except Exception:
            log_feishu.exception("后台任务异常: channel=%s", channel_id)
            try:
                await adapter.send(OutgoingMessage(
                    channel_id=channel_id,
                    text=f"后台任务失败: {task_desc[:50]}",
                    reply_to=incoming.message_id,
                ))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 普通对话处理
# ---------------------------------------------------------------------------

async def _handle_chat(incoming, adapter, conn):
    """处理普通对话消息：reaction → CC 执行 → 回复 → 移除 reaction。"""
    from brain.channels.base import OutgoingMessage
    from brain.executor.cc import execute
    from brain.memory.extractor import extract_and_store
    from brain.memory.retriever import build_memory_context
    from brain.session.manager import get_active_session, get_workspace, save_session, touch_session

    channel_id = incoming.channel_id
    reaction_id = None

    try:
        reaction_id = await adapter.add_reaction(incoming.message_id)

        workspace = get_workspace(channel_id)

        session_id = get_active_session(conn, channel_id)
        if session_id:
            touch_session(conn, channel_id, session_id)
            log_feishu.info("复用 session: channel=%s, session=%s", channel_id, session_id)

        memory_context = build_memory_context(conn, incoming.text)

        new_session_id, result_text = await execute(
            prompt=incoming.text,
            cwd=workspace,
            channel_id=channel_id,
            system_append=memory_context,
            resume=session_id,
        )

        if new_session_id:
            save_session(conn, channel_id, new_session_id)

        reply_text = result_text if result_text else "（CC 未返回结果）"
        await adapter.send(OutgoingMessage(
            channel_id=channel_id,
            text=reply_text,
            reply_to=incoming.message_id,
        ))

        if result_text:
            extract_and_store(conn, result_text, source=f"channel:{channel_id}")

    except Exception:
        log_feishu.exception("处理消息异常: channel=%s", channel_id)
        try:
            await adapter.send(OutgoingMessage(
                channel_id=channel_id,
                text="处理消息时发生错误，请稍后重试。",
                reply_to=incoming.message_id,
            ))
        except Exception:
            pass
    finally:
        if reaction_id:
            await adapter.remove_reaction(incoming.message_id, reaction_id)


# ---------------------------------------------------------------------------
# 启动时更新 workspace 模板
# ---------------------------------------------------------------------------

def _reinit_all_workspaces():
    """启动时将所有已有 workspace 的模板更新为最新版本。"""
    import shutil
    from brain.config import RESOURCE_DIR
    from brain.session.manager import _inject_channel_id

    template_dir = RESOURCE_DIR / "template"
    if not template_dir.exists() or not WORKSPACE_BASE.exists():
        return

    workspaces = [d for d in WORKSPACE_BASE.iterdir() if d.is_dir()]
    if not workspaces:
        return

    for ws in workspaces:
        for src in template_dir.iterdir():
            if src.is_file():
                shutil.copy2(src, ws / src.name)
        _inject_channel_id(ws, ws.name)

    log.info("已更新 %d 个 workspace 模板", len(workspaces))


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
    _reinit_all_workspaces()
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
        feishu_adapter = FeishuAdapter(
            FEISHU_APP_ID, FEISHU_APP_SECRET,
            allowed_users=FEISHU_ALLOWED_USERS or None,
        )
        feishu_adapter.on_message(
            lambda msg: _dispatch_message(msg, feishu_adapter, conn)
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
