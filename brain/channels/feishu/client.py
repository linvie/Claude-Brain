"""飞书 API 客户端 — 封装消息发送/回复/编辑操作。"""

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    UpdateMessageRequest,
    UpdateMessageRequestBody,
)

from brain.infra.logger import log_feishu as log


class FeishuClient:
    """飞书 API 客户端，提供消息发送/回复/编辑能力。"""

    def __init__(self, app_id: str, app_secret: str):
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

    def send_text(self, chat_id: str, text: str) -> str:
        """发送文本消息，返回 message_id。"""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not response.success():
            log.error("飞书发送消息失败: code=%s, msg=%s", response.code, response.msg)
            raise RuntimeError(f"feishu send failed: {response.code} {response.msg}")
        return response.data.message_id

    def reply_text(self, message_id: str, text: str) -> str:
        """回复消息，返回新 message_id。"""
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.reply(request)
        if not response.success():
            log.error("飞书回复消息失败: code=%s, msg=%s", response.code, response.msg)
            raise RuntimeError(f"feishu reply failed: {response.code} {response.msg}")
        return response.data.message_id

    def edit_text(self, message_id: str, text: str) -> None:
        """编辑已发消息内容。"""
        request = (
            UpdateMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                UpdateMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.update(request)
        if not response.success():
            log.error("飞书编辑消息失败: code=%s, msg=%s", response.code, response.msg)
            raise RuntimeError(f"feishu edit failed: {response.code} {response.msg}")
