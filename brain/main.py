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
    NOTION_PROJECT_DB_ID,
    NOTION_TASK_DB_ID,
    WORKSPACE_BASE,
)
from brain.core.dispatcher import dispatch, ensure_setup_task
from brain.core.outbox import check_all_outboxes
from brain.core.tester import check_tester_stops
from brain.core.watchdog import watchdog
from brain.infra.db import get_db, running_task_count
from brain.infra.logger import log, log_feishu, log_scheduler
from brain.integrations.notion import fetch_ready_tasks, list_active_existing_projects

_shutdown_event: asyncio.Event | None = None


# ---------------------------------------------------------------------------
# 信号处理 & task 异常兜底
# ---------------------------------------------------------------------------

def _handle_signal(sig: signal.Signals):  # pragma: no cover
    name = signal.Signals(sig).name
    log.info("收到 %s 信号，准备关闭", name)
    if _shutdown_event:
        _shutdown_event.set()


def _on_task_exception(task: asyncio.Task):  # pragma: no cover
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("async task [%s] 异常退出: %s", task.get_name(), exc, exc_info=exc)


# ---------------------------------------------------------------------------
# v1: Notion 轮询
# ---------------------------------------------------------------------------

async def _notion_poll_loop(conn, shutdown: asyncio.Event):  # pragma: no cover
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

        # existing 项目自动创建迁移任务（独立于任务分发，解决鸡生蛋问题）
        try:
            for proj in list_active_existing_projects():
                ensure_setup_task(conn, proj["project_id"], proj)
        except Exception:
            log_scheduler.exception("检查 existing 项目迁移任务失败")

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
_INSTANT_COMMANDS = {"/reset", "/status", "/help", "/model", "/usage"}
# 需要 CC 但不阻塞队列的命令
_BACKGROUND_COMMANDS = {"/btw", "/doctor"}
# /btw 并发限制
_BTW_SEMAPHORE = asyncio.Semaphore(3)
# /doctor 并发限制（避免多个诊断同时跑）
_DOCTOR_SEMAPHORE = asyncio.Semaphore(1)

# channel_id → asyncio.Queue，只有普通对话消息才入队
_channel_queues: dict[str, asyncio.Queue] = {}
_channel_workers: dict[str, asyncio.Task] = {}

# 最近活跃的飞书 channel_id（供 v1 任务通知使用）
_last_active_chat_id: str = ""


def get_notify_chat_id() -> str:
    """获取飞书通知 chat_id：优先用配置值，否则用最近活跃的 channel。"""
    from brain.config import FEISHU_NOTIFY_CHAT_ID
    return FEISHU_NOTIFY_CHAT_ID or _last_active_chat_id


async def _dispatch_message(incoming, adapter, conn):  # pragma: no cover
    """消息分流：命令立即处理，对话排队。"""
    global _last_active_chat_id
    _last_active_chat_id = incoming.channel_id

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


async def _enqueue_chat(incoming, adapter, conn):  # pragma: no cover
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


async def _channel_worker(channel_id: str, adapter, conn):  # pragma: no cover
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

async def _handle_command(incoming, adapter, conn):  # pragma: no cover
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

    elif cmd == "/doctor":
        # 诊断：独立 CC 进程跑，不复用 channel session（即使当前 session 死了也能跑）
        placeholder_id = await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text="🩺 正在诊断（约 30-60 秒）...",
            reply_to=incoming.message_id,
        ))
        asyncio.create_task(
            _run_doctor_task(incoming, adapter, placeholder_id),
            name=f"doctor-{incoming.channel_id[:8]}",
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
        from brain.executor.cc import get_session_info
        info = get_session_info(incoming.channel_id)
        if info["connected"]:
            import datetime
            last = datetime.datetime.fromtimestamp(info["last_activity"]).strftime("%H:%M:%S")
            status = (
                f"**Session 状态**\n\n"
                f"- Session: `{info['session_id'] or '无'}`\n"
                f"- CC 连接: 已连接\n"
                f"- 模型: {info['model']}\n"
                f"- 本次累计: {info['total_queries']} 次查询, ${info['total_cost']}\n"
                f"- 最近活动: {last}"
            )
        else:
            from brain.session.manager import get_active_session
            session_id = get_active_session(conn, incoming.channel_id)
            status = (
                f"**Session 状态**\n\n"
                f"- Session: `{session_id or '无'}`\n"
                f"- CC 连接: 未连接（下次消息时自动连接）"
            )
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text=status,
            reply_to=incoming.message_id,
        ))

    elif cmd == "/model":
        from brain.executor.cc import get_session_info, set_model
        parts_model = arg.strip().split(None, 1) if arg.strip() else []
        sub = parts_model[0].lower() if parts_model else ""

        if sub == "switch" and len(parts_model) > 1:
            # /model switch <name>
            name = parts_model[1].strip()
            model = name if name.lower() != "default" else None
            await set_model(incoming.channel_id, model)
            await adapter.send(OutgoingMessage(
                channel_id=incoming.channel_id,
                text=f"模型已切换: **{name}**（下条消息生效）",
                reply_to=incoming.message_id,
            ))
        else:
            # /model — 显示当前模型和可用列表
            info = get_session_info(incoming.channel_id)
            current = info.get("model", "default")
            await adapter.send(OutgoingMessage(
                channel_id=incoming.channel_id,
                text=(
                    f"**当前模型:** {current}\n\n"
                    "**可用 alias（自动指向最新版本）：**\n"
                    "- `opus` — 最强\n"
                    "- `sonnet` — 平衡\n"
                    "- `haiku` — 快速\n"
                    "- `opus[1m]` — 最强 + 1M context\n"
                    "- `sonnet[1m]` — 平衡 + 1M context\n"
                    "- `default` — 恢复账户默认\n\n"
                    "也可直接用完整 model ID，如 `claude-sonnet-4-6`\n\n"
                    "切换: `/model switch opus`"
                ),
                reply_to=incoming.message_id,
            ))

    elif cmd == "/usage":
        from brain.executor.cc import get_session_info
        info = get_session_info(incoming.channel_id)
        usage_text = (
            f"**用量统计**\n\n"
            f"- 查询次数: {info.get('total_queries', 0)}\n"
            f"- 累计费用: ${info.get('total_cost', 0)}\n"
            f"- 当前模型: {info.get('model', 'default')}"
        )
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text=usage_text,
            reply_to=incoming.message_id,
        ))

    elif cmd == "/help":
        help_text = (
            "**可用命令：**\n\n"
            "- `/btw <任务>` — 后台执行任务（不阻塞对话）\n"
            "- `/doctor` — 独立诊断系统状态（出错时使用）\n"
            "- `/model` — 查看当前模型和可用列表\n"
            "- `/model switch <name>` — 切换模型（sonnet/opus/haiku/default）\n"
            "- `/usage` — 查看用量统计\n"
            "- `/status` — 查看 session 详细状态\n"
            "- `/reset` — 重置对话 session\n"
            "- `/help` — 显示此帮助\n\n"
            "直接发消息即对话（线性处理，排队执行）。"
        )
        await adapter.send(OutgoingMessage(
            channel_id=incoming.channel_id,
            text=help_text,
            reply_to=incoming.message_id,
        ))

    # 不会走到这里，因为只有 _KNOWN_COMMANDS 才进入 _handle_command


_DOCTOR_SYSTEM_APPEND = """
你是 CCBrain 诊断助手。你被独立调起来分析系统问题，**不属于任何用户对话**。

可用资源：
- ~/.ccbrain/logs/ 下的 *.log 文件（cc.log, feishu.log, launchd.stderr.log, scheduler.log, notion.log, memory.log, session.log）
- ~/.ccbrain/state.db（SQLite，task_runs/v2_sessions/memories 表）

工作流程：
1. 用 tail/grep 读取最近 30 分钟的相关日志
2. 找到 ERROR 级别条目和异常 traceback
3. 关联 cc.log 和 feishu.log 时间戳，重建事件时间线
4. 判断根因（subprocess 崩溃 / context 超限 / 网络问题 / 权限问题 / 其他）
5. 给出处理建议（/reset / restart / 等待 / 升级版本）

输出格式（Markdown，控制在 500 字以内）：

## 时间线
（关键事件按时间排序）

## 根因
（一句话判断 + 证据）

## 建议
（用户应该做什么）

约束：
- **不要修改任何文件**，只读分析
- 如果近 30 分钟无 ERROR，回复 "系统状态正常，最近 30 分钟无异常"
- 不要执行 ccbrain 的 install/restart/reset 等命令，只做诊断
"""

_DOCTOR_PROMPT = "请诊断 CCBrain 最近 30 分钟的状态。"


async def _run_doctor_task(incoming, adapter, placeholder_id):  # pragma: no cover
    """独立诊断任务：用 one_shot_query 跑全新 CC 进程，结果更新到占位卡片。"""
    from brain.executor.cc import one_shot_query
    from brain.session.manager import get_workspace

    channel_id = incoming.channel_id

    async with _DOCTOR_SEMAPHORE:
        workspace = get_workspace(channel_id)
        try:
            result = await one_shot_query(
                prompt=_DOCTOR_PROMPT,
                cwd=workspace,
                system_append=_DOCTOR_SYSTEM_APPEND,
                timeout=120.0,
            )
            final_text = f"**🩺 诊断报告**\n\n{result}" if result else "🩺 诊断未返回结果"
            if placeholder_id:
                await adapter.patch_card(placeholder_id, final_text)
        except Exception:
            log_feishu.exception("doctor 任务异常: channel=%s", channel_id)
            if placeholder_id:
                try:
                    await adapter.patch_card(placeholder_id, "🩺 诊断失败")
                except Exception:
                    pass


async def _run_background_task(incoming, adapter, conn, task_desc: str):  # pragma: no cover
    """后台执行 CC 任务，完成后回复。最多 3 个并发，超出排队等待。"""
    from brain.channels.base import OutgoingMessage
    from brain.executor.cc import execute
    from brain.session.manager import get_workspace

    channel_id = incoming.channel_id

    async with _BTW_SEMAPHORE:
        workspace = get_workspace(channel_id)
        try:
            _, result_text, _meta = await execute(
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


def _build_footer(metadata: dict) -> str | None:
    """从 execute() 返回的 metadata 构建卡片 footer 文本。"""
    parts = []
    duration_ms = metadata.get("duration_ms", 0)
    if duration_ms:
        parts.append(f"耗时 {duration_ms / 1000:.0f}s")
    model = metadata.get("model")
    if model:
        parts.append(model)
    cost = metadata.get("total_cost_usd", 0)
    if cost:
        parts.append(f"${cost:.4f}")
    return " · ".join(parts) if parts else None


async def _handle_chat(incoming, adapter, conn):  # pragma: no cover
    """处理普通对话消息：占位卡片 → 流式更新 → 最终结果。"""
    from brain.channels.base import OutgoingMessage
    from brain.executor.cc import execute
    from brain.memory.extractor import extract_and_store
    from brain.memory.retriever import build_memory_context
    from brain.session.manager import get_active_session, get_workspace, save_session, touch_session

    channel_id = incoming.channel_id
    card_msg_id = None
    reaction_id = None

    try:
        # 0. Typing reaction（思考指示器）
        reaction_id = await adapter.add_reaction(incoming.message_id)

        # 1. 发送占位卡片（后续流式更新此卡片）
        placeholder = OutgoingMessage(
            channel_id=channel_id,
            text="**思考中...**",
            reply_to=incoming.message_id,
        )
        card_msg_id = await adapter.send(placeholder)
        log_feishu.info("占位卡片已发送: msg_id=%s", card_msg_id)

        # 2. 准备 session + 记忆
        workspace = get_workspace(channel_id)

        session_id = get_active_session(conn, channel_id)
        if session_id:
            touch_session(conn, channel_id, session_id)
            log_feishu.info("复用 session: channel=%s, session=%s", channel_id, session_id)

        memory_context = build_memory_context(conn, incoming.text, channel_id=channel_id)

        # 2.5 构建 system_append（记忆 + 飞书 chat_id + Notion context）
        lark_context = (
            f"\n\n## 飞书通知\n"
            f"当前对话 chat_id: {channel_id}\n"
            "你的回复会自动通过卡片流式展示给用户（每 2 秒更新），无需手动发送常规进度。\n"
            "**仅在以下情况使用 lark-cli 发独立消息**（会产生新消息气泡）：\n"
            "- 遇到阻碍，需要用户回复才能继续\n"
            "- 需要用户确认危险操作\n"
            f"命令: lark-cli im send --receive-id \"{channel_id}\" "
            f"--receive-id-type chat_id --msg-type text "
            f"--content '{{\"text\":\"你的消息\"}}'\n"
        )
        notion_context = ""
        if NOTION_ENABLED and NOTION_TASK_DB_ID:
            notion_context = (
                f"\n\n## Notion 集成\n"
                f"- Task 数据库 ID: {NOTION_TASK_DB_ID}\n"
                f"- Project 数据库 ID: {NOTION_PROJECT_DB_ID}\n"
                "你可以用 mcp__notion__* 工具操作 Notion。详见 notion_config.json。\n"
            )
        system_append = memory_context + lark_context + notion_context

        # 3. 流式回调：更新占位卡片内容
        async def _on_stream(text: str):
            if card_msg_id:
                log_feishu.info("流式更新: msg_id=%s, len=%d", card_msg_id, len(text))
                await adapter.patch_card(card_msg_id, text + "\n\n_生成中..._")

        # 4. 执行 CC（流式）
        new_session_id, result_text, metadata = await execute(
            prompt=incoming.text,
            cwd=workspace,
            channel_id=channel_id,
            system_append=system_append,
            resume=session_id,
            on_stream=_on_stream,
        )

        if new_session_id:
            save_session(conn, channel_id, new_session_id)

        # 5. 最终更新卡片为完整结果（含 footer 元信息）
        final_text = result_text if result_text else "⚠️ CC 未返回结果（可能 context 超限）。请尝试 /reset 开新会话。"
        footer = _build_footer(metadata) if metadata else None
        if card_msg_id:
            await adapter.patch_card(card_msg_id, final_text, footer=footer)

        # 6. 移除 typing reaction
        if reaction_id:
            await adapter.remove_reaction(incoming.message_id, reaction_id)
            reaction_id = None

        # 7. 提取记忆
        if result_text:
            extract_and_store(conn, result_text, source=f"channel:{channel_id}")

    except Exception:
        log_feishu.exception("处理消息异常: channel=%s", channel_id)
        try:
            if reaction_id:
                await adapter.remove_reaction(incoming.message_id, reaction_id)
        except Exception:
            pass
        try:
            if card_msg_id:
                await adapter.patch_card(card_msg_id, "处理消息时发生错误，请稍后重试。")
            else:
                await adapter.send(OutgoingMessage(
                    channel_id=channel_id,
                    text="处理消息时发生错误，请稍后重试。",
                    reply_to=incoming.message_id,
                ))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 启动时更新 workspace 模板
# ---------------------------------------------------------------------------

def _reinit_all_workspaces():  # pragma: no cover
    """启动时更新所有 workspace 的模板区域（保留用户自定义内容）。"""
    from brain.session.manager import update_workspace_template

    if not WORKSPACE_BASE.exists():
        return

    workspaces = [d for d in WORKSPACE_BASE.iterdir() if d.is_dir()]
    if not workspaces:
        return

    for ws in workspaces:
        update_workspace_template(ws, ws.name)

    log.info("已更新 %d 个 workspace 模板", len(workspaces))


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

async def main():  # pragma: no cover
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
