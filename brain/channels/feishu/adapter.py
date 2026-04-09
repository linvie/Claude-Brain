"""飞书 Channel Adapter — WebSocket 长连接模式收发消息。"""

from __future__ import annotations

import asyncio
import json
import time

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from brain.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from brain.channels.feishu.client import FeishuClient
from brain.infra.logger import log_feishu as log


class FeishuAdapter(ChannelAdapter):
    """飞书 adapter：通过 WebSocket 长连接接收消息，同步 API 发送/编辑消息。"""

    def __init__(self, app_id: str, app_secret: str):
        super().__init__()
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = FeishuClient(app_id, app_secret)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_client: lark.ws.Client | None = None

    def _on_receive(self, data: P2ImMessageReceiveV1) -> None:
        """WebSocket 消息回调（在 ws 线程中执行）。"""
        msg = data.event.message
        sender = data.event.sender

        # 只处理文本消息
        if msg.message_type != "text":
            log.debug("[feishu] 忽略非文本消息: type=%s", msg.message_type)
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

        log.info("[feishu] 收到消息: channel=%s, user=%s, text=%s",
                 incoming.channel_id, incoming.user_id, text[:50])

        # 从 ws 线程调度到 asyncio 事件循环
        if self._callback and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._callback(incoming),
                self._loop,
            )

    async def start(self):
        """启动 WebSocket 长连接（在线程中运行，不阻塞事件循环）。"""
        self._loop = asyncio.get_running_loop()

        # 注册事件处理器
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_receive)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )

        log.info("[feishu] 正在连接 WebSocket...")
        # ws.Client.start() 是阻塞的，放到线程里跑
        await self._loop.run_in_executor(None, self._ws_client.start)

    async def stop(self):
        """停止 adapter（ws client 没有优雅关闭方法，依赖进程退出）。"""
        log.info("[feishu] adapter 停止")

    async def send(self, msg: OutgoingMessage) -> str:
        """发送消息（在线程中执行同步 API 调用）。"""
        loop = asyncio.get_running_loop()
        if msg.reply_to:
            return await loop.run_in_executor(
                None, self._client.reply_text, msg.reply_to, msg.text
            )
        return await loop.run_in_executor(
            None, self._client.send_text, msg.channel_id, msg.text
        )

    async def edit(self, message_id: str, text: str) -> None:
        """编辑消息（在线程中执行同步 API 调用）。"""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._client.edit_text, message_id, text
        )
