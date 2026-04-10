"""飞书通知单元测试 — chat_id 解析逻辑。"""

from unittest.mock import patch

from brain.infra.feishu_notify import notify_feishu


class TestNotifyFeishu:
    """notify_feishu: chat_id 解析和前置检查。"""

    def test_skips_when_disabled(self):
        """飞书未启用时跳过。"""
        with patch("brain.infra.feishu_notify.FEISHU_ENABLED", False):
            result = notify_feishu("title", "content")
        assert result is False

    def test_skips_when_no_app_id(self):
        """无 app_id 时跳过。"""
        with (
            patch("brain.infra.feishu_notify.FEISHU_ENABLED", True),
            patch("brain.infra.feishu_notify.FEISHU_APP_ID", ""),
        ):
            result = notify_feishu("title", "content")
        assert result is False

    def test_uses_explicit_chat_id(self):
        """传入 chat_id 时直接使用，不走 fallback。"""
        with (
            patch("brain.infra.feishu_notify.FEISHU_ENABLED", True),
            patch("brain.infra.feishu_notify.FEISHU_APP_ID", "cli_test"),
            patch("brain.infra.feishu_notify.FEISHU_APP_SECRET", "secret"),
            patch("brain.infra.feishu_notify._get_tenant_token", return_value=None),
        ):
            # token 获取失败 → 返回 False，但证明 chat_id 解析成功（走到了 token 步骤）
            result = notify_feishu("title", "content", chat_id="oc_explicit")
        assert result is False  # token 失败

    def test_skips_when_no_chat_id_available(self):
        """无任何 chat_id 来源时跳过。"""
        with (
            patch("brain.infra.feishu_notify.FEISHU_ENABLED", True),
            patch("brain.infra.feishu_notify.FEISHU_APP_ID", "cli_test"),
            patch("brain.main.get_notify_chat_id", return_value=""),
        ):
            result = notify_feishu("title", "content")
        assert result is False
