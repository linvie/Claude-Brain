"""飞书 Channel Adapter — WebSocket 长连接模式收发消息。"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from brain.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from brain.channels.feishu.client import FeishuClient
from brain.infra.logger import log_feishu as log


class FeishuAdapter(ChannelAdapter):
    """飞书 adapter：通过 WebSocket 长连接接收消息，同步 API 发送/编辑消息。"""

    def __init__(self, app_id: str, app_secret: str, allowed_users: list[str] | None = None):
        super().__init__()
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = FeishuClient(app_id, app_secret)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._allowed_users: set[str] | None = set(allowed_users) if allowed_users else None

    def _on_receive(self, data) -> None:
        """WebSocket 消息回调（在 ws 线程中执行）。"""
        msg = data.event.message
        sender = data.event.sender

        if msg.message_type != "text":
            log.debug("忽略非文本消息: type=%s", msg.message_type)
            return

        # allowlist 检查
        user_id = sender.sender_id.open_id
        if self._allowed_users and user_id not in self._allowed_users:
            log.info("拒绝未授权用户: user=%s, chat=%s", user_id, msg.chat_id)
            return

        text = json.loads(msg.content).get("text", "")
        if not text.strip():
            return

        incoming = IncomingMessage(
            channel_id=msg.chat_id,
            user_id=sender.sender_id.open_id,
            message_id=msg.message_id,
            text=text,
            platform="feishu",
            timestamp=time.time(),
        )

        log.info("收到消息: channel=%s, user=%s, text=%s",
                 incoming.channel_id, incoming.user_id, text[:50])

        if self._callback and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._callback(incoming),
                self._loop,
            )

    def _ws_thread_main(self):
        """在独立线程 + 独立 event loop 中运行飞书 WebSocket。

        lark_oapi.ws.client 模块在 import 时用 asyncio.get_event_loop() 缓存了
        主线程的 event loop 到模块级变量 `loop`。在新线程中直接 patch 这个变量，
        让 ws.Client.start() 使用新线程的 event loop。
        """
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)

        # 直接 patch 模块级 loop 变量
        import lark_oapi.ws.client as ws_mod
        ws_mod.loop = new_loop

        import lark_oapi as lark
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_receive)
            .build()
        )

        # 重新构造 Client（让 asyncio.Lock 绑定到新 loop）
        ws_client = ws_mod.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )

        log.info("WebSocket 线程启动，正在连接...")
        ws_client.start()

    async def start(self):
        """启动 WebSocket 长连接（在独立线程中运行）。"""
        self._loop = asyncio.get_running_loop()

        log.info("正在启动飞书 WebSocket...")
        thread = threading.Thread(target=self._ws_thread_main, daemon=True)
        thread.start()

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        log.info("adapter 停止")

    async def send(self, msg: OutgoingMessage) -> str:
        loop = asyncio.get_running_loop()
        if msg.reply_to:
            return await loop.run_in_executor(
                None, self._client.reply_text, msg.reply_to, msg.text
            )
        return await loop.run_in_executor(
            None, self._client.send_text, msg.channel_id, msg.text
        )

    async def edit(self, message_id: str, text: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._client.edit_text, message_id, text
        )

    async def add_reaction(self, message_id: str) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._client.add_reaction, message_id, "OnIt"
        )

    async def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._client.remove_reaction, message_id, reaction_id
        )
