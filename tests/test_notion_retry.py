"""Tests for Notion API retry logic in brain.integrations.notion."""

from unittest.mock import MagicMock, patch

import pytest
import requests

# The module reads CONFIG at import time, so we must patch before importing.
_FAKE_NOTION_CFG = {
    "notion": {
        "token": "fake-token",
        "task_db_id": "fake-task-db",
        "project_db_id": "fake-project-db",
    },
}


@pytest.fixture(autouse=True)
def _patch_config():
    """Patch CONFIG so the module can be imported without a real config file."""
    with patch.dict("brain.config.CONFIG", _FAKE_NOTION_CFG, clear=True):
        yield


# ---------------------------------------------------------------------------
# _call_with_retry unit tests
# ---------------------------------------------------------------------------


class TestCallWithRetry:
    def _get_call_with_retry(self):
        from brain.integrations.notion import _call_with_retry
        return _call_with_retry

    def test_success_on_first_attempt(self):
        fn = MagicMock(return_value="ok")
        result = self._get_call_with_retry()(fn, "a", key="b")
        assert result == "ok"
        fn.assert_called_once_with("a", key="b")

    def test_retries_on_connection_error_then_succeeds(self):
        fn = MagicMock(side_effect=[
            requests.exceptions.ConnectionError("conn reset"),
            "ok",
        ])
        with patch("brain.integrations.notion.time.sleep") as mock_sleep:
            result = self._get_call_with_retry()(fn)
        assert result == "ok"
        assert fn.call_count == 2
        mock_sleep.assert_called_once()

    def test_retries_on_timeout_then_succeeds(self):
        fn = MagicMock(side_effect=[
            requests.exceptions.Timeout("read timed out"),
            "ok",
        ])
        with patch("brain.integrations.notion.time.sleep"):
            result = self._get_call_with_retry()(fn)
        assert result == "ok"
        assert fn.call_count == 2

    def test_retries_on_ssl_error_then_succeeds(self):
        # SSLError is a subclass of ConnectionError
        fn = MagicMock(side_effect=[
            requests.exceptions.SSLError("SSLEOFError"),
            "ok",
        ])
        with patch("brain.integrations.notion.time.sleep"):
            result = self._get_call_with_retry()(fn)
        assert result == "ok"
        assert fn.call_count == 2

    def test_raises_after_all_retries_exhausted(self):
        exc = requests.exceptions.ConnectionError("persistent failure")
        fn = MagicMock(side_effect=exc)
        with (
            patch("brain.integrations.notion.time.sleep"),
            pytest.raises(requests.exceptions.ConnectionError, match="persistent failure"),
        ):
            self._get_call_with_retry()(fn)
        assert fn.call_count == 3  # _RETRY_ATTEMPTS

    def test_non_retryable_error_propagates_immediately(self):
        fn = MagicMock(side_effect=requests.exceptions.HTTPError("404 Not Found"))
        with pytest.raises(requests.exceptions.HTTPError, match="404"):
            self._get_call_with_retry()(fn)
        fn.assert_called_once()  # no retry

    def test_sleep_delay_between_retries(self):
        fn = MagicMock(side_effect=[
            requests.exceptions.ConnectionError("err1"),
            requests.exceptions.ConnectionError("err2"),
            "ok",
        ])
        with patch("brain.integrations.notion.time.sleep") as mock_sleep:
            result = self._get_call_with_retry()(fn)
        assert result == "ok"
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert call[0][0] == 1.5  # _RETRY_DELAY


# ---------------------------------------------------------------------------
# Wrapper function integration tests
# ---------------------------------------------------------------------------


class TestWrapperRetry:
    """Verify wrapper functions retry on network errors and return fallbacks."""

    def test_update_status_retries_and_succeeds(self):
        from brain.integrations import notion

        notion._client = MagicMock()
        notion._client.update_task_status.side_effect = [
            requests.exceptions.ConnectionError("reset"),
            None,  # success
        ]
        with patch("brain.integrations.notion.time.sleep"):
            notion.update_status("task-1", "Done")
        assert notion._client.update_task_status.call_count == 2

    def test_append_log_retries_and_succeeds(self):
        from brain.integrations import notion

        notion._client = MagicMock()
        notion._client.append_execution_log.side_effect = [
            requests.exceptions.SSLError("SSLEOFError"),
            None,
        ]
        with patch("brain.integrations.notion.time.sleep"):
            notion.append_log("task-1", "some log")
        assert notion._client.append_execution_log.call_count == 2

    def test_update_status_returns_none_after_retries_exhausted(self):
        from brain.integrations import notion

        notion._client = MagicMock()
        notion._client.update_task_status.side_effect = requests.exceptions.ConnectionError("down")
        with patch("brain.integrations.notion.time.sleep"):
            # Should not raise — wrapper catches and logs
            notion.update_status("task-1", "Done")
        assert notion._client.update_task_status.call_count == 3

    def test_fetch_ready_tasks_returns_empty_after_retries(self):
        from brain.integrations import notion

        notion._client = MagicMock()
        notion._client.query_ready_tasks.side_effect = requests.exceptions.Timeout("timeout")
        with patch("brain.integrations.notion.time.sleep"):
            result = notion.fetch_ready_tasks()
        assert result == []
        assert notion._client.query_ready_tasks.call_count == 3

    def test_get_task_status_returns_none_after_retries(self):
        from brain.integrations import notion

        notion._client = MagicMock()
        notion._client.get_task_status.side_effect = requests.exceptions.ConnectionError("err")
        with patch("brain.integrations.notion.time.sleep"):
            result = notion.get_task_status("task-1")
        assert result is None

    def test_http_error_not_retried(self):
        """HTTP 4xx errors should not be retried."""
        from brain.integrations import notion

        notion._client = MagicMock()
        notion._client.update_task_status.side_effect = requests.exceptions.HTTPError("400 Bad Request")
        notion.update_status("task-1", "Done")
        # Called only once — no retry for HTTP errors
        notion._client.update_task_status.assert_called_once()
