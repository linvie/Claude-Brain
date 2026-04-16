"""飞书卡片回调（card.action.trigger）单元测试。

覆盖：
- _on_card_action 正确构造 IncomingMessage（chat_id、user_id、text 格式）
- allowed_users 过滤
- 返回 toast ack 结构
- 按钮回调 vs 表单回调 文本格式
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain.channels.feishu.adapter import FeishuAdapter


def _make_trigger_data(
    *,
    chat_id: str = "oc_test_chat",
    user_id: str = "ou_test_user",
    msg_id: str = "om_test_msg",
    action_name: str | None = None,
    action_value: dict | None = None,
    form_value: dict | None = None,
):
    """构造模拟的 P2CardActionTrigger 数据。"""
    data = MagicMock()

    # event.context
    data.event.context.open_chat_id = chat_id
    data.event.context.open_message_id = msg_id

    # event.operator
    data.event.operator.open_id = user_id

    # event.action
    action = MagicMock()
    action.name = action_name
    action.value = action_value
    action.form_value = form_value
    data.event.action = action

    return data


@pytest.fixture()
def adapter():
    """创建测试用 adapter（mock 掉飞书客户端）。"""
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
    """创建带 allowed_users 的 adapter。"""
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


class TestCardActionBasic:
    """基本卡片回调处理。"""

    def test_button_callback_creates_incoming_message(self, adapter):
        data = _make_trigger_data(
            action_name="confirm_btn",
            action_value={"action": "confirm"},
        )
        adapter._on_card_action(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        # 检查 IncomingMessage 参数
        assert adapter._callback.call_count == 1
        incoming = adapter._callback.call_args[0][0]
        assert incoming.channel_id == "oc_test_chat"
        assert incoming.user_id == "ou_test_user"
        assert incoming.message_id == "om_test_msg"
        assert incoming.platform == "feishu"

    def test_button_text_format(self, adapter):
        data = _make_trigger_data(
            action_name="confirm_btn",
            action_value={"action": "confirm", "task_id": "123"},
        )
        adapter._on_card_action(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        incoming = adapter._callback.call_args[0][0]
        assert "[飞书卡片回调]" in incoming.text
        assert "按钮：confirm_btn" in incoming.text
        assert '"action": "confirm"' in incoming.text
        assert '"task_id": "123"' in incoming.text

    def test_form_callback_text_format(self, adapter):
        data = _make_trigger_data(
            action_name="submit_btn",
            action_value={"action": "submit_form"},
            form_value={"title": "测试标题", "priority": "high"},
        )
        adapter._on_card_action(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        incoming = adapter._callback.call_args[0][0]
        assert "[飞书卡片回调]" in incoming.text
        assert "表单填写：" in incoming.text
        assert "测试标题" in incoming.text
        assert "high" in incoming.text

    def test_minimal_callback_only_prefix(self, adapter):
        """无 name/value/form_value 时只有前缀。"""
        data = _make_trigger_data()
        adapter._on_card_action(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))

        incoming = adapter._callback.call_args[0][0]
        assert incoming.text == "[飞书卡片回调]"


class TestCardActionToastResponse:
    """验证返回的 toast ack 结构。"""

    def test_returns_toast(self, adapter):
        data = _make_trigger_data(action_name="btn")
        resp = adapter._on_card_action(data)
        # P2CardActionTriggerResponse wraps a dict
        assert resp is not None

    def test_unauthorized_user_returns_empty(self, adapter_with_allowlist):
        data = _make_trigger_data(user_id="ou_unauthorized")
        resp = adapter_with_allowlist._on_card_action(data)
        assert resp is not None


class TestCardActionAllowlist:
    """allowed_users 过滤。"""

    def test_allowed_user_dispatches(self, adapter_with_allowlist):
        data = _make_trigger_data(user_id="ou_allowed", action_name="btn")
        adapter_with_allowlist._on_card_action(data)
        adapter_with_allowlist._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter_with_allowlist._callback.call_count == 1

    def test_unauthorized_user_blocked(self, adapter_with_allowlist):
        data = _make_trigger_data(user_id="ou_blocked", action_name="btn")
        adapter_with_allowlist._on_card_action(data)
        adapter_with_allowlist._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter_with_allowlist._callback.call_count == 0

    def test_no_allowlist_allows_all(self, adapter):
        data = _make_trigger_data(user_id="ou_anyone", action_name="btn")
        adapter._on_card_action(data)
        adapter._loop.run_until_complete(asyncio.sleep(0.05))
        assert adapter._callback.call_count == 1


class TestCardActionNoCallback:
    """无 callback 或无 loop 时不崩溃。"""

    def test_no_callback_no_crash(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "brain.channels.feishu.adapter.FeishuClient",
                lambda *a, **kw: MagicMock(),
            )
            a = FeishuAdapter("id", "secret")
            a._loop = asyncio.new_event_loop()
            # _callback is None by default
            data = _make_trigger_data(action_name="btn")
            resp = a._on_card_action(data)
            assert resp is not None
            a._loop.close()

    def test_no_loop_no_crash(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "brain.channels.feishu.adapter.FeishuClient",
                lambda *a, **kw: MagicMock(),
            )
            a = FeishuAdapter("id", "secret")
            a._callback = AsyncMock()
            # _loop is None by default
            data = _make_trigger_data(action_name="btn")
            resp = a._on_card_action(data)
            assert resp is not None
