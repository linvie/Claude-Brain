"""Channel 抽象层 — 定义标准消息格式和 adapter 接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class IncomingMessage:
    """标准化入站消息。"""
    channel_id: str       # 会话标识（群组 ID 或私聊 ID）
    user_id: str          # 发送者标识
    message_id: str       # 消息 ID（用于回复/编辑）
    text: str             # 消息文本
    platform: str         # 平台标识（'feishu', 'telegram', ...）
    timestamp: float = 0  # 消息时间戳


@dataclass
class OutgoingMessage:
    """标准化出站消息。"""
    channel_id: str
    text: str
    reply_to: str | None = None  # 回复哪条消息


# 消息回调类型：接收 IncomingMessage，异步处理
MessageCallback = Callable[[IncomingMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    """Channel adapter 抽象基类。每种平台实现一个子类。"""

    def __init__(self):
        self._callback: MessageCallback | None = None

    def on_message(self, callback: MessageCallback):
        """注册消息回调。"""
        self._callback = callback

    @abstractmethod
    async def start(self):
        """启动连接（WebSocket/webhook/polling）。"""
        ...

    @abstractmethod
    async def stop(self):
        """断开连接。"""
        ...

    @abstractmethod
    async def send(self, msg: OutgoingMessage) -> str:
        """发送消息，返回 message_id。"""
        ...

    @abstractmethod
    async def edit(self, message_id: str, text: str) -> None:
        """编辑已发消息（流式更新用）。"""
        ...

    async def add_reaction(self, message_id: str) -> str | None:
        """添加"思考中"emoji reaction，返回 reaction_id。"""
        return None

    async def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """移除 emoji reaction。"""
        pass
