"""飞书通知 — 轻量级发送函数，供 v1/v2 共用。

位于 infra 层（共享），避免 v1 core 模块 import v2 channels 模块。
"""

import json

import requests

from brain.config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_ENABLED, FEISHU_NOTIFY_CHAT_ID
from brain.infra.logger import log


def notify_feishu(title: str, content: str) -> bool:
    """发送飞书通知卡片（非阻塞，失败静默）。

    Returns:
        True 如果发送成功，False 如果跳过或失败。
    """
    if not (FEISHU_ENABLED and FEISHU_NOTIFY_CHAT_ID and FEISHU_APP_ID):
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
                "receive_id": FEISHU_NOTIFY_CHAT_ID,
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("code") == 0:
            log.info("[notify] 飞书通知已发送: %s", title)
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
