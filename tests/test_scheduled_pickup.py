"""scheduled_at 定时拾取功能测试。"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from brain.infra.db import all_done, init_db
from brain.integrations.notion import NotionClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_notion_page(
    task_id: str,
    task_name: str,
    status: str = "Ready",
    project_id: str = "proj-1",
    scheduled_at: str | None = None,
    blocked_by: list[str] | None = None,
) -> dict:
    """构造一个最小化的 Notion page dict 供 _page_to_task 测试。"""
    props = {
        "task_name": {"title": [{"plain_text": task_name}]},
        "description": {"rich_text": [{"plain_text": "test desc"}]},
        "task_type": {"select": {"name": "executor"}},
        "priority": {"select": {"name": "Normal"}},
        "status": {"select": {"name": status}},
        "project": {"relation": [{"id": project_id}]},
        "blocked_by": {"relation": [{"id": bid} for bid in (blocked_by or [])]},
        "scheduled_at": {
            "date": {"start": scheduled_at} if scheduled_at else None,
        },
    }
    return {"id": task_id, "properties": props}


def _make_client() -> NotionClient:
    """创建一个不会真正调 API 的 NotionClient。"""
    client = NotionClient.__new__(NotionClient)
    client.token = "fake"
    client.task_db_id = "db-1"
    client.project_db_id = "db-2"
    client.headers = {}
    return client


def _get_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# _extract_date 测试
# ---------------------------------------------------------------------------

class TestExtractDate:
    def test_with_datetime(self):
        prop = {"date": {"start": "2026-04-24T10:00:00+08:00"}}
        assert NotionClient._extract_date(prop) == "2026-04-24T10:00:00+08:00"

    def test_with_date_only(self):
        prop = {"date": {"start": "2026-04-24"}}
        assert NotionClient._extract_date(prop) == "2026-04-24"

    def test_empty(self):
        prop = {"date": None}
        assert NotionClient._extract_date(prop) is None

    def test_missing_date_key(self):
        prop = {}
        assert NotionClient._extract_date(prop) is None


# ---------------------------------------------------------------------------
# _page_to_task 测试：scheduled_at 和 status 提取
# ---------------------------------------------------------------------------

class TestPageToTaskScheduledAt:
    def setup_method(self):
        self.client = _make_client()
        # Mock get_project 避免 HTTP 调用
        self.client.get_project = MagicMock(return_value={
            "properties": {"repo_url": {"url": "https://github.com/test/repo"}}
        })

    def test_extracts_scheduled_at(self):
        page = _make_notion_page("t1", "task1", scheduled_at="2026-04-24T02:00:00Z")
        task = self.client._page_to_task(page)
        assert task is not None
        assert task["scheduled_at"] == "2026-04-24T02:00:00Z"

    def test_scheduled_at_none_when_empty(self):
        page = _make_notion_page("t2", "task2")
        task = self.client._page_to_task(page)
        assert task is not None
        assert task["scheduled_at"] is None

    def test_extracts_status(self):
        page = _make_notion_page("t3", "task3", status="Pending")
        task = self.client._page_to_task(page)
        assert task is not None
        assert task["status"] == "Pending"

    def test_ready_status(self):
        page = _make_notion_page("t4", "task4", status="Ready")
        task = self.client._page_to_task(page)
        assert task is not None
        assert task["status"] == "Ready"


# ---------------------------------------------------------------------------
# query_ready_tasks 查询 payload 测试
# ---------------------------------------------------------------------------

class TestQueryPayload:
    def test_query_includes_scheduled_pending_filter(self):
        """验证 Notion API 查询 payload 包含 scheduled Pending 条件。"""
        client = _make_client()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("brain.integrations.notion.requests.post", return_value=mock_resp) as mock_post:
            client.query_ready_tasks()

        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        filter_ = payload["filter"]

        # 顶层是 "or"
        assert "or" in filter_
        conditions = filter_["or"]
        assert len(conditions) == 2

        # 第一个条件：status=Ready
        assert conditions[0]["property"] == "status"
        assert conditions[0]["select"]["equals"] == "Ready"

        # 第二个条件：and[status=Pending, scheduled_at not empty, scheduled_at <= now]
        and_conds = conditions[1]["and"]
        assert len(and_conds) == 3
        assert and_conds[0]["property"] == "status"
        assert and_conds[0]["select"]["equals"] == "Pending"
        assert and_conds[1]["property"] == "scheduled_at"
        assert "is_not_empty" in and_conds[1]["date"]
        assert and_conds[2]["property"] == "scheduled_at"
        assert "on_or_before" in and_conds[2]["date"]


# ---------------------------------------------------------------------------
# Dispatcher 拾取逻辑测试（blocked_by 交互）
# ---------------------------------------------------------------------------

class TestDispatchScheduledTask:
    """测试 dispatch() 对 scheduled Pending 任务的处理。"""

    def setup_method(self):
        self.conn = _get_test_db()

    def teardown_method(self):
        self.conn.close()

    @patch("brain.core.dispatcher.launch_cc", return_value=12345)
    @patch("brain.core.dispatcher.setup_workspace")
    @patch("brain.core.dispatcher.prepare_workspace", return_value=MagicMock())
    @patch("brain.core.dispatcher.get_page_body", return_value="")
    @patch("brain.core.dispatcher.get_related_tasks", return_value=[])
    @patch("brain.core.dispatcher.get_project_info", return_value={"project_name": "test", "project_description": "", "repo_url": None, "project_type": "new"})
    @patch("brain.core.dispatcher.update_status")
    @patch("brain.core.dispatcher.append_log")
    def test_scheduled_pending_task_dispatched(self, *mocks):
        """Pending + scheduled_at 到期的任务正常分发。"""
        from brain.core.dispatcher import dispatch

        task = {
            "task_id": "t-scheduled",
            "task_name": "scheduled task",
            "project_id": "proj-1",
            "task_type": "executor",
            "blocked_by": [],
            "status": "Pending",
            "scheduled_at": "2026-04-24T00:00:00Z",
        }
        dispatch(self.conn, task)

        # 验证任务被记录为 running
        row = self.conn.execute(
            "SELECT status FROM task_runs WHERE task_id = ?", ("t-scheduled",)
        ).fetchone()
        assert row is not None
        assert row["status"] == "running"

    @patch("brain.core.dispatcher.launch_cc")
    @patch("brain.core.dispatcher.setup_workspace")
    @patch("brain.core.dispatcher.prepare_workspace")
    @patch("brain.core.dispatcher.get_page_body", return_value="")
    @patch("brain.core.dispatcher.get_related_tasks", return_value=[])
    @patch("brain.core.dispatcher.get_project_info", return_value={"project_name": "test", "project_description": "", "repo_url": None, "project_type": "new"})
    @patch("brain.core.dispatcher.update_status")
    @patch("brain.core.dispatcher.append_log")
    def test_blocked_scheduled_task_not_dispatched(self, *mocks):
        """Pending + scheduled_at 到期但 blocked_by 未 Done → 不分发。"""
        from brain.core.dispatcher import dispatch

        task = {
            "task_id": "t-blocked",
            "task_name": "blocked scheduled task",
            "project_id": "proj-1",
            "task_type": "executor",
            "blocked_by": ["t-dep-1"],
            "status": "Pending",
            "scheduled_at": "2026-04-24T00:00:00Z",
        }
        dispatch(self.conn, task)

        # 依赖未 Done，不应分发
        row = self.conn.execute(
            "SELECT status FROM task_runs WHERE task_id = ?", ("t-blocked",)
        ).fetchone()
        assert row is None

    @patch("brain.core.dispatcher.launch_cc", return_value=12345)
    @patch("brain.core.dispatcher.setup_workspace")
    @patch("brain.core.dispatcher.prepare_workspace", return_value=MagicMock())
    @patch("brain.core.dispatcher.get_page_body", return_value="")
    @patch("brain.core.dispatcher.get_related_tasks", return_value=[])
    @patch("brain.core.dispatcher.get_project_info", return_value={"project_name": "test", "project_description": "", "repo_url": None, "project_type": "new"})
    @patch("brain.core.dispatcher.update_status")
    @patch("brain.core.dispatcher.append_log")
    def test_blocked_scheduled_task_dispatched_when_dep_done(self, *mocks):
        """Pending + scheduled_at 到期 + blocked_by 全 Done → 正常分发。"""
        from brain.core.dispatcher import dispatch

        # 先把依赖任务标记为 done
        self.conn.execute(
            "INSERT INTO task_runs (task_id, project_id, status, workspace_path) VALUES (?, ?, ?, ?)",
            ("t-dep-1", "proj-1", "done", "/tmp/ws"),
        )
        self.conn.commit()

        task = {
            "task_id": "t-scheduled-2",
            "task_name": "scheduled with dep done",
            "project_id": "proj-1",
            "task_type": "executor",
            "blocked_by": ["t-dep-1"],
            "status": "Pending",
            "scheduled_at": "2026-04-24T00:00:00Z",
        }
        dispatch(self.conn, task)

        row = self.conn.execute(
            "SELECT status FROM task_runs WHERE task_id = ?", ("t-scheduled-2",)
        ).fetchone()
        assert row is not None
        assert row["status"] == "running"

    @patch("brain.core.dispatcher.launch_cc", return_value=99999)
    @patch("brain.core.dispatcher.setup_workspace")
    @patch("brain.core.dispatcher.prepare_workspace", return_value=MagicMock())
    @patch("brain.core.dispatcher.get_page_body", return_value="")
    @patch("brain.core.dispatcher.get_related_tasks", return_value=[])
    @patch("brain.core.dispatcher.get_project_info", return_value={"project_name": "test", "project_description": "", "repo_url": None, "project_type": "new"})
    @patch("brain.core.dispatcher.update_status")
    @patch("brain.core.dispatcher.append_log")
    def test_ready_task_still_dispatched(self, *mocks):
        """普通 Ready 任务不受影响，正常分发。"""
        from brain.core.dispatcher import dispatch

        task = {
            "task_id": "t-ready",
            "task_name": "ready task",
            "project_id": "proj-2",
            "task_type": "executor",
            "blocked_by": [],
            "status": "Ready",
            "scheduled_at": None,
        }
        dispatch(self.conn, task)

        row = self.conn.execute(
            "SELECT status FROM task_runs WHERE task_id = ?", ("t-ready",)
        ).fetchone()
        assert row is not None
        assert row["status"] == "running"
