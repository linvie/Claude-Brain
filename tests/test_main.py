"""Main 模块单元测试 — notify chat_id 解析。"""

from unittest.mock import patch

from brain import main as brain_main


class TestGetNotifyChatId:
    """get_notify_chat_id: 动态获取飞书通知 chat_id。"""

    def test_returns_config_value_first(self):
        """配置值优先于 last_active。"""
        brain_main._last_active_chat_id = "oc_active"
        with patch("brain.config.FEISHU_NOTIFY_CHAT_ID", "oc_config"):
            result = brain_main.get_notify_chat_id()
        assert result == "oc_config"
        brain_main._last_active_chat_id = ""

    def test_falls_back_to_last_active(self):
        """配置为空时用最近活跃 channel。"""
        brain_main._last_active_chat_id = "oc_active"
        with patch("brain.config.FEISHU_NOTIFY_CHAT_ID", ""):
            result = brain_main.get_notify_chat_id()
        assert result == "oc_active"
        brain_main._last_active_chat_id = ""

    def test_returns_empty_when_nothing(self):
        """都没有时返回空字符串。"""
        brain_main._last_active_chat_id = ""
        with patch("brain.config.FEISHU_NOTIFY_CHAT_ID", ""):
            result = brain_main.get_notify_chat_id()
        assert result == ""
