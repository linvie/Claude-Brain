"""飞书 post（富文本）消息处理单元测试。

覆盖：
- _extract_post_text 从各种 post 结构中正确提取纯文本
- _on_receive 对 post 类型消息正确派发 IncomingMessage
- 群聊 @bot 过滤、allowlist 过滤对 post 消息同样生效
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain.channels.feishu.adapter import FeishuAdapter, _extract_post_text

# ---------------------------------------------------------------------------
# _extract_post_text 单元测试
# ---------------------------------------------------------------------------


class TestExtractPostText:
    """纯函数 _extract_post_text 各种输入格式。"""

    def test_simple_text_paragraph(self):
        content = {
            "title": "",
            "content": [[{"tag": "text", "text": "你好世界"}]],
        }
        assert _extract_post_text(content) == "你好世界"

    def test_title_included(self):
        content = {
            "title": "公告标题",
            "content": [[{"tag": "text", "text": "正文内容"}]],
        }
        result = _extract_post_text(content)
        assert "公告标题" in result
        assert "正文内容" in result

    def test_multi_paragraph(self):
        content = {
            "title": "",
            "content": [
                [{"tag": "text", "text": "第一段"}],
                [{"tag": "text", "text": "第二段"}],
            ],
        }
        result = _extract_post_text(content)
        assert "第一段" in result
        assert "第二段" in result
        assert result == "第一段\n第二段"

    def test_mixed_elements(self):
        """段落内包含 text + a + at 混合元素。"""
        content = {
            "title": "",
            "content": [
                [
                    {"tag": "text", "text": "请查看 "},
                    {"tag": "a", "text": "链接", "href": "https://example.com"},
                    {"tag": "text", "text": " 和 "},
                    {"tag": "at", "user_id": "ou_xxx", "user_name": "张三"},
                ],
            ],
        }
        result = _extract_post_text(content)
        assert result == "请查看 链接 和 张三"

    def test_link_without_text_uses_href(self):
        content = {
            "title": "",
            "content": [[{"tag": "a", "href": "https://example.com"}]],
        }
        assert _extract_post_text(content) == "https://example.com"

    def test_language_partitioned_format(self):
        """飞书 post 可能按语言分区: {"zh_cn": {"title": ..., "content": [...]}}。"""
        content = {
            "zh_cn": {
                "title": "中文标题",
                "content": [[{"tag": "text", "text": "中文内容"}]],
            },
        }
        result = _extract_post_text(content)
        assert "中文标题" in result
        assert "中文内容" in result

    def test_empty_content(self):
        content = {"title": "", "content": []}
        assert _extract_post_text(content) == ""

    def test_unknown_tags_ignored(self):
        content = {
            "title": "",
            "content": [
                [
                    {"tag": "text", "text": "有内容"},
                    {"tag": "img", "image_key": "xxx"},
                    {"tag": "emotion", "emoji_type": "SMILE"},
                ],
            ],
        }
        assert _extract_post_text(content) == "有内容"

    def test_at_without_username(self):
        """@提及没有 user_name 时返回空字符串。"""
        content = {
            "title": "",
            "content": [[{"tag": "at", "user_id": "ou_xxx"}]],
        }
        # at without user_name produces empty string, whole line is empty → no output
        assert _extract_post_text(content) == ""


# ---------------------------------------------------------------------------
# _on_receive post 消息集成测试
# ---------------------------------------------------------------------------


def _make_msg_data(
    *,
    msg_type: str = "post",
    content: dict | str | None = None,
    chat_id: str = "oc_test_chat",
    user_id: str = "ou_test_user",
    msg_id: str = "om_test_msg",
    chat_type: str = "p2p",
    mentions: list | None = None,
):
    """构造模拟的 WebSocket 消息数据。"""
    if content is None:
        content = {
            "title": "",
            "content": [[{"tag": "text", "text": "你好"}]],
        }
    data = MagicMock()
    data.event.message.message_type = msg_type
    data.event.message.chat_id = chat_id
    data.event.message.message_id = msg_id
    data.event.message.content = json.dumps(content) if isinstance(content, dict) else content
    data.event.message.chat_type = chat_type
    data.event.message.mentions = mentions
    data.event.sender.sender_id.open_id = user_id
    return data


@pytest.fixture()
def adapter():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "brain.channels.feishu.adapter.FeishuClient",
            lambda *a, **kw: MagicMock(),
        )
        a = FeishuAdapter("test_id", "test_secret")
        a._loop = asyncio.new_event_loop()
        a._callback = AsyncMock()
        yield a
        a._loop.close()


@pytest.fixture()
def adapter_with_allowlist():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "brain.channels.feishu.adapter.FeishuClient",
            lambda *a, **kw: MagicMock(),
        )
        a = FeishuAdapter("test_id", "test_secret", allowed_users=["ou_allowed"])
        a._loop = asyncio.new_event_loop()
        a._callback = AsyncMock()
        yield a
        a._loop.close()


class TestOnReceivePost:
    """_on_receive 对 post 消息的处理。"""

    def test_post_message_dispatched(self, adapter):
        data = _make_msg_data(
            content={
                "title": "",
                "content": [[{"tag": "text", "text": "你好世界"}]],
            },
        )
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        assert adapter._callback.call_count == 1
        incoming = adapter._callback.call_args[0][0]
        assert incoming.text == "你好世界"
        assert incoming.channel_id == "oc_test_chat"
        assert incoming.user_id == "ou_test_user"

    def test_post_with_title(self, adapter):
        data = _make_msg_data(
            content={
                "title": "重要通知",
                "content": [[{"tag": "text", "text": "明天放假"}]],
            },
        )
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        incoming = adapter._callback.call_args[0][0]
        assert "重要通知" in incoming.text
        assert "明天放假" in incoming.text

    def test_post_with_mixed_elements(self, adapter):
        data = _make_msg_data(
            content={
                "title": "",
                "content": [
                    [
                        {"tag": "text", "text": "请看 "},
                        {"tag": "a", "text": "这里", "href": "https://x.com"},
                    ],
                ],
            },
        )
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        incoming = adapter._callback.call_args[0][0]
        assert incoming.text == "请看 这里"

    def test_empty_post_ignored(self, adapter):
        data = _make_msg_data(content={"title": "", "content": []})
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter._callback.call_count == 0

    def test_text_message_still_works(self, adapter):
        """确保原有的 text 类型消息未受影响。"""
        data = _make_msg_data(
            msg_type="text",
            content=json.dumps({"text": "纯文本消息"}),
        )
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        assert adapter._callback.call_count == 1
        incoming = adapter._callback.call_args[0][0]
        assert incoming.text == "纯文本消息"

    def test_image_message_still_ignored(self, adapter):
        """非 text/post 类型消息仍被忽略。"""
        data = _make_msg_data(msg_type="image", content=json.dumps({"image_key": "xxx"}))
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter._callback.call_count == 0


class TestOnReceivePostFilters:
    """post 消息的 allowlist 和群聊过滤。"""

    def test_allowlist_blocks_unauthorized_post(self, adapter_with_allowlist):
        data = _make_msg_data(user_id="ou_blocked")
        adapter_with_allowlist._on_receive(data)
        adapter_with_allowlist._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter_with_allowlist._callback.call_count == 0

    def test_allowlist_allows_authorized_post(self, adapter_with_allowlist):
        data = _make_msg_data(user_id="ou_allowed")
        adapter_with_allowlist._on_receive(data)
        adapter_with_allowlist._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter_with_allowlist._callback.call_count == 1

    def test_group_post_without_mention_ignored(self, adapter):
        data = _make_msg_data(chat_type="group", mentions=None)
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter._callback.call_count == 0

    def test_group_post_with_mention_dispatched(self, adapter):
        data = _make_msg_data(
            chat_type="group",
            mentions=[MagicMock()],
        )
        adapter._on_receive(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter._callback.call_count == 1
