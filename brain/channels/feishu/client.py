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
    """适配飞书卡片 markdown 渲染的限制。

    飞书卡片 markdown 支持：**加粗** *斜体* ~~删除线~~ `代码` 代码块 - 列表 [链接](url)
    不支持：## 标题、| 表格 |、> 引用、图片
    """
    import re

    # HTML 标签
    text = text.replace("<details>", "").replace("</details>", "")
    text = text.replace("<summary>", "**").replace("</summary>", "**\n")

    # ## 标题 → **加粗**（飞书不支持 # 标题语法）
    text = re.sub(r"^#{1,6}\s+(.+)$", r"**\1**", text, flags=re.MULTILINE)

    # > 引用 → 去掉 > 前缀（飞书部分版本不支持）
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)

    # 表格 → 转为列表格式（飞书卡片不支持表格）
    lines = text.split("\n")
    result = []
    table_headers = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        # 检测表格分隔行 |---|---|
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            in_table = True
            continue
        # 检测表格行 | x | y |
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                # 表头行
                table_headers = cells
                in_table = True
            else:
                # 数据行 → 转为 "- key: value" 列表
                if table_headers and len(table_headers) == len(cells):
                    parts = [f"{h}: {c}" for h, c in zip(table_headers, cells) if c]
                    result.append("- " + " | ".join(parts))
                else:
                    result.append("- " + " | ".join(cells))
            continue

        # 非表格行
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
    def _build_card(text: str, title: str | None = None) -> str:
        """将 markdown 文本包装为飞书 Interactive Card JSON。

        飞书 markdown 限制：
        - 单个 markdown 元素最大 10000 字符
        - 不支持 HTML 标签
        - 表格支持有限，过宽会截断
        """
        # 飞书 markdown 适配
        text = _optimize_markdown(text)

        elements = []

        # 标题
        if title:
            elements.append({
                "tag": "markdown",
                "content": f"**{title}**",
            })
            elements.append({"tag": "hr"})

        # 内容（超长时分段，飞书单 markdown 元素限 10000 字符）
        if len(text) <= 9000:
            elements.append({"tag": "markdown", "content": text})
        else:
            # 按段落分割
            chunks = _split_markdown(text, 9000)
            for chunk in chunks:
                elements.append({"tag": "markdown", "content": chunk})

        card = {
            "config": {"update_multi": True},
            "elements": elements,
        }
        return json.dumps(card)

    def send_text(self, chat_id: str, text: str) -> str:
        """发送 markdown 卡片消息，返回 message_id。"""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(self._build_card(text))
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
        """回复 markdown 卡片消息，返回新 message_id。"""
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(self._build_card(text))
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

    def patch_card(self, message_id: str, text: str) -> None:
        """更新已发的 interactive card 内容（用于流式输出）。"""
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(self._build_card(text))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.patch(request)
        if not response.success():
            log.warning("patch card 失败: code=%s, msg=%s, log_id=%s",
                        response.code, response.msg, response.get_log_id())

    def add_reaction(self, message_id: str, emoji_type: str = "OnIt") -> str | None:
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
