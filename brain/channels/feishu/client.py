"""飞书 API 客户端 — 封装消息发送/回复/编辑操作。"""

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    UpdateMessageRequest,
    UpdateMessageRequestBody,
)

from brain.infra.logger import log_feishu as log


def _optimize_markdown(text: str) -> str:
    """适配飞书卡片 schema 2.0 markdown 渲染。

    schema 2.0 支持：标题、表格、引用、加粗、斜体、删除线、代码、列表、链接
    仍不支持：HTML 标签（details/summary）
    """
    import re

    # HTML 标签（飞书卡片仍不支持）
    text = text.replace("<details>", "").replace("</details>", "")
    text = text.replace("<summary>", "**").replace("</summary>", "**\n")

    # 标题降级：H2~H6 → H5，H1 → H4（顺序重要：先匹配多 # 的）
    text = re.sub(r"^#{2,6} (.+)$", r"##### \1", text, flags=re.MULTILINE)
    text = re.sub(r"^# (.+)$", r"#### \1", text, flags=re.MULTILINE)

    # 引用、表格：schema 2.0 原生支持，保留不转换

    return text


def _table_to_list(text: str) -> str:
    """将 markdown 表格转为列表格式（用于表格渲染失败时降级）。"""
    import re

    lines = text.split("\n")
    result = []
    table_headers: list[str] = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            in_table = True
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                table_headers = cells
                in_table = True
            else:
                if table_headers and len(table_headers) == len(cells):
                    parts = [f"{h}: {c}" for h, c in zip(table_headers, cells) if c]
                    result.append("- " + " | ".join(parts))
                else:
                    result.append("- " + " | ".join(cells))
            continue

        if in_table:
            in_table = False
            table_headers = []
        result.append(line)

    return "\n".join(result)


def _split_markdown(text: str, max_len: int) -> list[str]:
    """按段落边界分割 markdown，每段不超过 max_len 字符。"""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > max_len and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:max_len]]


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

    @staticmethod
    def _build_card(text: str, title: str | None = None, footer: str | None = None) -> str:
        """将 markdown 文本包装为飞书 Interactive Card JSON（schema 2.0）。"""
        text = _optimize_markdown(text)

        elements = []

        if title:
            elements.append({"tag": "markdown", "content": f"#### {title}"})
            elements.append({"tag": "hr"})

        if len(text) <= 9000:
            elements.append({"tag": "markdown", "content": text})
        else:
            for chunk in _split_markdown(text, 9000):
                elements.append({"tag": "markdown", "content": chunk})

        if footer:
            elements.append({"tag": "hr"})
            elements.append({"tag": "markdown", "content": footer, "text_size": "notation"})

        card = {
            "schema": "2.0",
            "config": {"wide_screen_mode": True, "update_multi": True},
            "body": {"elements": elements},
        }
        return json.dumps(card)

    @staticmethod
    def _is_table_error(response) -> bool:
        """检查是否为表格渲染相关错误（可降级重试）。"""
        if response.code == 230099:
            return True
        return "11310" in str(response.msg or "")

    def send_text(self, chat_id: str, text: str, footer: str | None = None) -> str:
        """发送 markdown 卡片消息，返回 message_id。"""
        card_content = self._build_card(text, footer=footer)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(card_content)
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not response.success():
            if self._is_table_error(response):
                log.info("表格渲染失败，降级为列表格式重试")
                return self.send_text(chat_id, _table_to_list(text), footer=footer)
            log.error("飞书发送消息失败: code=%s, msg=%s", response.code, response.msg)
            raise RuntimeError(f"feishu send failed: {response.code} {response.msg}")
        return response.data.message_id

    def reply_text(self, message_id: str, text: str, footer: str | None = None) -> str:
        """回复 markdown 卡片消息，返回新 message_id。"""
        card_content = self._build_card(text, footer=footer)
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(card_content)
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.reply(request)
        if not response.success():
            if self._is_table_error(response):
                log.info("表格渲染失败，降级为列表格式重试")
                return self.reply_text(message_id, _table_to_list(text), footer=footer)
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

    def patch_card(self, message_id: str, text: str, footer: str | None = None) -> None:
        """更新已发的 interactive card 内容（用于流式输出）。"""
        card_content = self._build_card(text, footer=footer)
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(card_content)
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.patch(request)
        if not response.success():
            if self._is_table_error(response):
                log.info("patch card 表格降级重试: msg_id=%s", message_id)
                self.patch_card(message_id, _table_to_list(text), footer=footer)
                return
            log.warning("patch card 失败: code=%s, msg=%s, log_id=%s",
                        response.code, response.msg, response.get_log_id())

    def add_reaction(self, message_id: str, emoji_type: str = "Typing") -> str | None:
        """给消息添加 emoji reaction，返回 reaction_id（失败返回 None，不抛异常）。"""
        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type({"emoji_type": emoji_type})
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message_reaction.create(request)
        if not response.success():
            log.debug("添加 reaction 失败（非阻塞）: code=%s, msg=%s", response.code, response.msg)
            return None
        return response.data.reaction_id

    def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """移除 emoji reaction（失败静默，不阻塞流程）。"""
        request = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        response = self._client.im.v1.message_reaction.delete(request)
        if not response.success():
            log.debug("移除 reaction 失败（非阻塞）: code=%s, msg=%s", response.code, response.msg)
