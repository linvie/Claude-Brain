"""飞书通知 — 轻量级发送函数，供 v1/v2 共用。

位于 infra 层（共享），避免 v1 core 模块 import v2 channels 模块。
"""

import json

import requests

from brain.config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_ENABLED
from brain.infra.logger import log


def notify_feishu(title: str, content: str, chat_id: str = "") -> bool:
    """发送飞书通知卡片（非阻塞，失败静默）。

    Args:
        chat_id: 指定发送目标。为空时自动获取（配置值或最近活跃 channel）。

    Returns:
        True 如果发送成功，False 如果跳过或失败。
    """
    if not (FEISHU_ENABLED and FEISHU_APP_ID):
        return False

    # 动态获取 chat_id：优先参数传入 → 配置值 → 最近活跃 channel
    if not chat_id:
        try:
            from brain.main import get_notify_chat_id
            chat_id = get_notify_chat_id()
        except ImportError:
            from brain.config import FEISHU_NOTIFY_CHAT_ID
            chat_id = FEISHU_NOTIFY_CHAT_ID

    if not chat_id:
        log.debug("[notify] 无可用 chat_id，跳过飞书通知")
        return False

    try:
        token = _get_tenant_token()
        if not token:
            return False

        card = {
            "config": {"update_multi": True},
            "elements": [{"tag": "markdown", "content": content[:9000]}],
        }
        if title:
            card["elements"].insert(0, {"tag": "markdown", "content": f"**{title}**"})
            card["elements"].insert(1, {"tag": "hr"})

        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("code") == 0:
            log.info("[notify] 飞书通知已发送: %s → %s", title, chat_id[:16])
            return True
        log.warning("[notify] 飞书通知失败: %s", resp.text[:200])
        return False
    except Exception:
        log.warning("[notify] 飞书通知发送异常（非阻塞）")
        return False


def _get_tenant_token() -> str | None:
    """获取 tenant_access_token。"""
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return data["tenant_access_token"]
        log.warning("[notify] 获取 token 失败: %s", data.get("msg"))
        return None
    except Exception:
        return None
