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

    def get_project_info(self, project_id: str) -> dict:
        """获取项目描述等信息供 inbox 上下文使用。

        返回 dict 包含 project_name, project_description, repo_url, project_type。
        """
        log.info("获取项目上下文信息: project=%s", project_id)
        try:
            page = self.get_project(project_id)
            props = page.get("properties", {})
            return {
                "project_name": self._extract_title(props.get("project_name", {})),
                "project_description": self._extract_rich_text(props.get("description", {})),
                "repo_url": self._extract_url(props.get("repo_url", {})),
                "project_type": self._extract_select(props.get("project_type", {})),
            }
        except requests.HTTPError as e:
            log.warning("获取项目信息失败: project=%s, error=%s", project_id, e)
            return {"project_name": "", "project_description": "", "repo_url": None, "project_type": None}

    def query_active_existing_projects(self) -> list[dict]:
        """查询 Project 数据库中 status=Active, project_type=existing 的项目。

        返回 [{project_id, project_name, repo_url, project_type}, ...]。
        """
        log.info("查询 Active existing 项目: db=%s", self.project_db_id)
        payload = {
            "filter": {
                "and": [
                    {"property": "status", "select": {"equals": "Active"}},
                    {"property": "project_type", "select": {"equals": "existing"}},
                ],
            },
        }
        resp = requests.post(
            f"{API_BASE}/databases/{self.project_db_id}/query",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        pages = resp.json().get("results", [])
        log.info("查询完成，返回 %d 个 Active existing 项目", len(pages))

        projects = []
        for page in pages:
            props = page.get("properties", {})
            projects.append({
                "project_id": page["id"],
                "project_name": self._extract_title(props.get("project_name", {})),
                "repo_url": self._extract_url(props.get("repo_url", {})),
                "project_type": "existing",
            })
        return projects

    def get_related_tasks(self, project_id: str) -> list[dict]:
        """获取同项目其他任务摘要，供 inbox 上下文使用。"""
        log.info("获取关联任务: project=%s", project_id)
        try:
            payload = {
                "filter": {
                    "property": "project",
                    "relation": {"contains": project_id},
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

            tasks = []
            for page in pages:
                props = page.get("properties", {})
                task_name = self._extract_title(props.get("task_name", {}))
                status = self._extract_select(props.get("status", {}))
                summary = self._extract_rich_text(props.get("execution_log", {}))
                # 截取最后一行作为摘要
                if summary:
                    lines = summary.strip().splitlines()
                    summary = lines[-1] if lines else ""
                tasks.append({
                    "task_id": page["id"],
                    "task_name": task_name,
                    "status": status or "Pending",
                    "summary": summary,
                })
            return tasks
        except requests.HTTPError as e:
            log.warning("获取关联任务失败: project=%s, error=%s", project_id, e)
            return []

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

    def create_task(self, project_id: str, task_name: str, description: str,  # pragma: no cover
                    task_type: str = "executor", priority: str = "High",
                    status: str = "Pending") -> str | None:
        """在 Task 数据库创建一个新任务，返回 page_id。"""
        log.info("创建任务: project=%s, name=%s, type=%s", project_id, task_name, task_type)
        payload = {
            "parent": {"database_id": self.task_db_id},
            "properties": {
                "task_name": {"title": [{"text": {"content": task_name}}]},
                "description": {"rich_text": [{"text": {"content": description}}]},
                "task_type": {"select": {"name": task_type}},
                "project": {"relation": [{"id": project_id}]},
                "status": {"select": {"name": status}},
                "priority": {"select": {"name": priority}},
            },
        }
        resp = requests.post(
            f"{API_BASE}/pages",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        page_id = resp.json()["id"]
        log.info("任务创建成功: task=%s, page_id=%s", task_name, page_id)
        return page_id

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
    # 读取页面正文
    # ------------------------------------------------------------------

    def get_page_body(self, page_id: str, max_blocks: int = 100) -> str:
        """读取页面正文 blocks，返回纯文本。"""
        log.info("读取页面正文: page=%s", page_id)
        resp = requests.get(
            f"{API_BASE}/blocks/{page_id}/children?page_size={max_blocks}",
            headers=self.headers,
        )
        resp.raise_for_status()
        blocks = resp.json().get("results", [])

        lines = []
        for block in blocks:
            block_type = block.get("type", "")
            content = block.get(block_type, {})
            rich_texts = content.get("rich_text", [])
            text = "".join(rt["plain_text"] for rt in rich_texts)
            if text:
                lines.append(text)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 单字段查询
    # ------------------------------------------------------------------

    def get_task_status(self, task_id: str) -> str | None:
        """查询单个 Task 的当前 status。"""
        log.debug("查询任务状态: task=%s", task_id)
        resp = requests.get(
            f"{API_BASE}/pages/{task_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        props = resp.json().get("properties", {})
        return self._extract_select(props.get("status", {}))

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


def get_project_info(project_id: str) -> dict:
    """获取项目上下文信息（名称、描述、仓库地址）。"""
    try:
        return _client.get_project_info(project_id)
    except Exception as e:
        log.error("获取项目信息失败: project=%s, error=%s", project_id, e)
        return {"project_name": "", "project_description": "", "repo_url": None}


def get_related_tasks(project_id: str) -> list[dict]:
    """获取同项目其他任务摘要。"""
    try:
        return _client.get_related_tasks(project_id)
    except Exception as e:
        log.error("获取关联任务失败: project=%s, error=%s", project_id, e)
        return []


def get_page_body(page_id: str) -> str:
    """读取 Notion 页面正文 blocks，返回纯文本。"""
    try:
        return _client.get_page_body(page_id)
    except Exception as e:
        log.error("读取页面正文失败: page=%s, error=%s", page_id, e)
        return ""


def create_task(project_id: str, task_name: str, description: str, **kwargs) -> str | None:  # pragma: no cover
    """在 Notion Task 数据库创建任务，返回 page_id。"""
    try:
        return _client.create_task(project_id, task_name, description, **kwargs)
    except Exception as e:
        log.error("创建任务失败: project=%s, name=%s, error=%s", project_id, task_name, e)
        return None


def get_task_status(task_id: str) -> str | None:
    """查询单个 Task 的当前 Notion status。"""
    try:
        return _client.get_task_status(task_id)
    except Exception as e:
        log.error("查询任务状态失败: task=%s, error=%s", task_id, e)
        return None


def list_active_existing_projects() -> list[dict]:
    """查询所有 Active 且 project_type=existing 的项目。"""
    try:
        return _client.query_active_existing_projects()
    except Exception as e:
        log.error("查询 Active existing 项目失败: %s", e)
        return []
