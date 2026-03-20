"""Notion REST API 集成 — NotionClient 类 + Brain 调用 wrapper 函数。"""

import logging

import requests

from brain.config import CONFIG

log = logging.getLogger("brain.notion")

API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# ---------------------------------------------------------------------------
# NotionClient
# ---------------------------------------------------------------------------


class NotionClient:
    def __init__(self, token: str, task_db_id: str, project_db_id: str):
        self.token = token
        self.task_db_id = task_db_id
        self.project_db_id = project_db_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def query_ready_tasks(self) -> list[dict]:
        """查询 Task 数据库中 status=Ready 的任务，按 priority 排序返回 task dict 列表。"""
        log.info("查询 Ready 任务: db=%s", self.task_db_id)

        payload = {
            "filter": {
                "property": "status",
                "select": {"equals": "Ready"},
            },
            "sorts": [
                {"property": "priority", "direction": "ascending"},
            ],
        }

        resp = requests.post(
            f"{API_BASE}/databases/{self.task_db_id}/query",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        pages = resp.json().get("results", [])
        log.info("查询完成，返回 %d 个 Ready 任务", len(pages))

        tasks = []
        for page in pages:
            task = self._page_to_task(page)
            if task:
                tasks.append(task)
        return tasks

    def get_project(self, project_id: str) -> dict:
        """获取 Project 页面信息。"""
        log.debug("获取 Project: %s", project_id)
        resp = requests.get(
            f"{API_BASE}/pages/{project_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def update_task_status(self, task_id: str, status: str):
        """更新 Task 的 status select 属性。"""
        log.info("更新状态: task=%s → %s", task_id, status)
        resp = requests.patch(
            f"{API_BASE}/pages/{task_id}",
            headers=self.headers,
            json={
                "properties": {
                    "status": {"select": {"name": status}},
                },
            },
        )
        resp.raise_for_status()
        log.debug("状态更新成功: task=%s → %s", task_id, status)

    def append_execution_log(self, task_id: str, entry: str):
        """向 Task 的 execution_log rich_text 属性追加一行日志。"""
        log.info("追加日志: task=%s, entry=%s", task_id, entry[:80])

        # 读取当前 execution_log
        resp = requests.get(
            f"{API_BASE}/pages/{task_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        props = resp.json().get("properties", {})
        existing = self._extract_rich_text(props.get("execution_log", {}))

        # 拼接新日志
        new_text = f"{existing}\n{entry}".strip()

        # 写回（Notion rich_text 上限 2000 字符，截断保留最新）
        if len(new_text) > 2000:
            new_text = new_text[-2000:]

        resp = requests.patch(
            f"{API_BASE}/pages/{task_id}",
            headers=self.headers,
            json={
                "properties": {
                    "execution_log": {
                        "rich_text": [{"type": "text", "text": {"content": new_text}}],
                    },
                },
            },
        )
        resp.raise_for_status()
        log.debug("日志追加成功: task=%s", task_id)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _page_to_task(self, page: dict) -> dict | None:
        """将 Notion page 转换为 Brain 内部 task dict。"""
        try:
            props = page["properties"]
            task_id = page["id"]
            task_name = self._extract_title(props.get("task_name", {}))
            description = self._extract_rich_text(props.get("description", {}))
            task_type = self._extract_select(props.get("task_type", {}))
            priority = self._extract_select(props.get("priority", {}))

            # project relation
            project_rel = props.get("project", {}).get("relation", [])
            project_id = project_rel[0]["id"] if project_rel else None
            if not project_id:
                log.warning("跳过无 project 关联的任务: %s (%s)", task_name, task_id)
                return None

            # blocked_by relation
            blocked_by_rel = props.get("blocked_by", {}).get("relation", [])
            blocked_by = [r["id"] for r in blocked_by_rel]

            # 获取 project 的 repo_url
            repo_url = None
            try:
                project_page = self.get_project(project_id)
                project_props = project_page.get("properties", {})
                repo_url = self._extract_url(project_props.get("repo_url", {}))
            except requests.HTTPError as e:
                log.warning("获取 project 信息失败: project=%s, error=%s", project_id, e)

            task = {
                "task_id": task_id,
                "task_name": task_name,
                "description": description,
                "task_type": task_type or "executor",
                "project_id": project_id,
                "blocked_by": blocked_by,
                "priority": priority or "Normal",
                "repo_url": repo_url,
            }
            log.debug("解析任务: %s (%s), type=%s, priority=%s", task_name, task_id, task_type, priority)
            return task

        except (KeyError, IndexError, TypeError) as e:
            log.error("解析 Notion page 失败: page_id=%s, error=%s", page.get("id"), e)
            return None

    @staticmethod
    def _extract_title(prop: dict) -> str:
        items = prop.get("title", [])
        return items[0]["plain_text"] if items else ""

    @staticmethod
    def _extract_rich_text(prop: dict) -> str:
        items = prop.get("rich_text", [])
        return "".join(item["plain_text"] for item in items)

    @staticmethod
    def _extract_select(prop: dict) -> str | None:
        sel = prop.get("select")
        return sel["name"] if sel else None

    @staticmethod
    def _extract_url(prop: dict) -> str | None:
        return prop.get("url")


# ---------------------------------------------------------------------------
# 模块级客户端实例
# ---------------------------------------------------------------------------

_notion_cfg = CONFIG["notion"]
_client = NotionClient(
    token=_notion_cfg["token"],
    task_db_id=_notion_cfg["task_db_id"],
    project_db_id=_notion_cfg["project_db_id"],
)


# ---------------------------------------------------------------------------
# Wrapper 函数（带错误处理，供其他模块调用）
# ---------------------------------------------------------------------------


def fetch_ready_tasks() -> list[dict]:
    """从 Notion Task 数据库获取所有 status=Ready 的任务，按 priority 排序。"""
    try:
        return _client.query_ready_tasks()
    except Exception as e:
        log.error("查询 Ready 任务失败: %s", e)
        return []


def update_status(task_id: str, status: str):
    """更新 Notion Task 的 status 字段。"""
    try:
        _client.update_task_status(task_id, status)
    except Exception as e:
        log.error("更新状态失败: task=%s, status=%s, error=%s", task_id, status, e)


def append_log(task_id: str, log_entry: str):
    """向 Notion Task 的 execution_log 字段追加一行日志。"""
    try:
        _client.append_execution_log(task_id, log_entry)
    except Exception as e:
        log.error("追加日志失败: task=%s, error=%s", task_id, e)
