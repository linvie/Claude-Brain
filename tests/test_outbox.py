"""outbox poller 测试 — 验证 TASK_DONE pr_url 守卫和重试逻辑。"""

import json
import sqlite3
from unittest.mock import patch

import pytest

from brain.core.outbox import (
    MAX_DONE_RETRIES,
    _done_retry_counts,
    check_all_outboxes,
    handle_outbox,
)


@pytest.fixture()
def db():
    """In-memory SQLite with task_runs schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE task_runs (
            task_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            pid INTEGER,
            start_time INTEGER,
            end_time INTEGER,
            task_type TEXT,
            task_name TEXT,
            summary TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE workspaces (
            project_id TEXT PRIMARY KEY,
            workspace_path TEXT NOT NULL,
            last_active INTEGER
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clear_retry_counts():
    """Reset retry counter between tests."""
    _done_retry_counts.clear()
    yield
    _done_retry_counts.clear()


@pytest.fixture()
def workspace(tmp_path):
    """Create a temp workspace directory."""
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _insert_running_task(conn, task_id="t1", workspace_path="/tmp/ws", pid=12345):
    conn.execute(
        "INSERT INTO task_runs (task_id, project_id, status, workspace_path, pid, start_time, task_name) "
        "VALUES (?, 'p1', 'running', ?, ?, 1000, 'test task')",
        (task_id, workspace_path, pid),
    )
    conn.commit()


def _make_done_outbox(pr_url="https://github.com/o/r/pull/1"):
    d = {
        "status": "TASK_DONE",
        "summary": "did something",
        "test_instructions": "run pytest",
    }
    if pr_url:
        d["pr_url"] = pr_url
    return json.dumps(d)


# ── TASK_DONE with pr_url: should process immediately ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_done_with_pr_url_processes(mock_kill, mock_append, mock_status, mock_notify, db, workspace):
    _insert_running_task(db, workspace_path=str(workspace))
    content = _make_done_outbox(pr_url="https://github.com/o/r/pull/42")

    result = handle_outbox(db, "t1", content)

    assert result is True
    mock_kill.assert_called_once()
    mock_status.assert_called_once_with("t1", "Done")
    row = db.execute("SELECT status FROM task_runs WHERE task_id = 't1'").fetchone()
    assert row["status"] == "done"


# ── TASK_DONE without pr_url: should defer ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_done_without_pr_url_defers(mock_kill, mock_append, mock_status, mock_notify, db):
    _insert_running_task(db)
    content = _make_done_outbox(pr_url="")

    result = handle_outbox(db, "t1", content)

    assert result is False
    mock_kill.assert_not_called()
    mock_status.assert_not_called()
    assert _done_retry_counts["t1"] == 1
    row = db.execute("SELECT status FROM task_runs WHERE task_id = 't1'").fetchone()
    assert row["status"] == "running"  # unchanged


# ── TASK_DONE without pr_url: increments retry counter ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_done_retry_counter_increments(mock_kill, mock_append, mock_status, mock_notify, db):
    _insert_running_task(db)
    content = _make_done_outbox(pr_url="")

    for i in range(1, MAX_DONE_RETRIES):
        result = handle_outbox(db, "t1", content)
        assert result is False
        assert _done_retry_counts["t1"] == i


# ── TASK_DONE without pr_url after MAX retries: marks Blocked ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_done_max_retries_marks_blocked(mock_kill, mock_append, mock_status, mock_notify, db):
    _insert_running_task(db)
    content = _make_done_outbox(pr_url="")

    # Exhaust retries
    for _ in range(MAX_DONE_RETRIES - 1):
        handle_outbox(db, "t1", content)

    result = handle_outbox(db, "t1", content)

    assert result is True
    mock_kill.assert_called_once()
    mock_status.assert_called_once_with("t1", "Blocked")
    row = db.execute("SELECT status FROM task_runs WHERE task_id = 't1'").fetchone()
    assert row["status"] == "blocked"
    assert "t1" not in _done_retry_counts  # cleaned up


# ── TASK_DONE: pr_url arrives on retry — clears counter ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_done_pr_url_arrives_on_retry_clears_counter(
    mock_kill, mock_append, mock_status, mock_notify, db, workspace
):
    _insert_running_task(db, workspace_path=str(workspace))
    no_pr = _make_done_outbox(pr_url="")
    with_pr = _make_done_outbox(pr_url="https://github.com/o/r/pull/99")

    # First two polls: no pr_url → defer
    handle_outbox(db, "t1", no_pr)
    handle_outbox(db, "t1", no_pr)
    assert _done_retry_counts["t1"] == 2

    # Third poll: pr_url present → process
    result = handle_outbox(db, "t1", with_pr)
    assert result is True
    assert "t1" not in _done_retry_counts
    mock_status.assert_called_once_with("t1", "Done")


# ── check_all_outboxes: doesn't clear outbox when deferred ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_check_all_outboxes_preserves_deferred(
    mock_kill, mock_append, mock_status, mock_notify, db, tmp_path
):
    ws = tmp_path / "ws1"
    ws.mkdir()
    _insert_running_task(db, workspace_path=str(ws))

    outbox_file = ws / "outbox.json"
    content = _make_done_outbox(pr_url="")
    outbox_file.write_text(content)

    check_all_outboxes(db)

    # outbox.json should NOT be cleared (deferred)
    assert outbox_file.read_text().strip() != "{}"
    assert json.loads(outbox_file.read_text())["status"] == "TASK_DONE"


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_check_all_outboxes_clears_after_done(
    mock_kill, mock_append, mock_status, mock_notify, db, tmp_path
):
    ws = tmp_path / "ws1"
    ws.mkdir()
    _insert_running_task(db, workspace_path=str(ws))

    outbox_file = ws / "outbox.json"
    content = _make_done_outbox(pr_url="https://github.com/o/r/pull/1")
    outbox_file.write_text(content)

    check_all_outboxes(db)

    # outbox.json SHOULD be cleared (processed)
    assert outbox_file.read_text().strip() == "{}"


# ── TASK_PROGRESS and TASK_BLOCKED still work ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_progress_returns_true(mock_kill, mock_append, mock_status, mock_notify, db):
    _insert_running_task(db)
    content = json.dumps({"status": "TASK_PROGRESS", "stage": "coding", "summary": "halfway done"})

    result = handle_outbox(db, "t1", content)

    assert result is True
    mock_kill.assert_not_called()


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
@patch("brain.core.outbox._kill_cc_process")
def test_blocked_returns_true(mock_kill, mock_append, mock_status, mock_notify, db):
    _insert_running_task(db)
    content = json.dumps({"status": "TASK_BLOCKED", "reason": "missing dep", "summary": "stuck"})

    result = handle_outbox(db, "t1", content)

    assert result is True
    mock_kill.assert_called_once()
    mock_status.assert_called_once_with("t1", "Blocked")


# ── Format error still handled ──


@patch("brain.core.outbox._notify_feishu")
@patch("brain.core.outbox.update_status")
@patch("brain.core.outbox.append_log")
def test_format_error_returns_true(mock_append, mock_status, mock_notify, db):
    _insert_running_task(db)
    content = '{"status": "INVALID"}'

    result = handle_outbox(db, "t1", content)

    assert result is True
    row = db.execute("SELECT status FROM task_runs WHERE task_id = 't1'").fetchone()
    assert row["status"] == "format_error"
