"""memory_sessions 表 + _LiveSession 追踪方法测试。"""

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from brain.executor import cc
from brain.infra.db import _init_memory_sessions, _init_v2_tables


def _make_conn():
    """创建带 memory_sessions 表的内存 DB。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_memory_sessions(conn)
    return conn


class TestInitMemorySessions:
    """_init_memory_sessions: 表创建 + 幂等。"""

    def test_creates_table(self):
        conn = _make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_sessions)").fetchall()}
        assert cols == {
            "session_id", "channel_id", "opened_at", "closed_at",
            "jsonl_path", "summarized_at", "extracted_at", "view_generated_at",
            "message_count",
        }
        conn.close()

    def test_idempotent(self):
        conn = sqlite3.connect(":memory:")
        _init_memory_sessions(conn)
        _init_memory_sessions(conn)  # 第二次不应报错
        conn.close()


class TestRecordSessionOpen:
    """_LiveSession._record_session_open: INSERT memory_sessions。"""

    def test_inserts_row(self):
        session = cc._LiveSession("ch-test", Path("/tmp"), "")
        conn = _make_conn()

        with patch("brain.infra.db.get_db", return_value=conn):
            session._record_session_open()

        rows = conn.execute("SELECT * FROM memory_sessions").fetchall()
        assert len(rows) == 1
        assert rows[0]["channel_id"] == "ch-test"
        assert rows[0]["closed_at"] is None
        conn.close()

    def test_stores_db_session_id(self):
        session = cc._LiveSession("ch-db", Path("/tmp"), "")
        conn = _make_conn()

        with patch("brain.infra.db.get_db", return_value=conn):
            session._record_session_open()

        assert session._db_session_id is not None
        assert session._db_session_id.startswith("ch-db:")
        conn.close()

    def test_does_not_raise_on_error(self):
        session = cc._LiveSession("ch-err", Path("/tmp"), "")
        with patch("brain.infra.db.get_db", side_effect=Exception("db error")):
            session._record_session_open()  # no exception


class TestRecordSessionClose:
    """_LiveSession._record_session_close: 归档 + UPDATE。"""

    def test_updates_closed_at(self):
        session = cc._LiveSession("ch-close", Path("/tmp"), "")
        session.session_id = "sdk-session-1"

        conn = _make_conn()
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at) VALUES (?, ?, ?)",
            ("ch-close:12345", "ch-close", int(time.time())),
        )
        conn.commit()

        with (
            patch("brain.infra.db.get_db", return_value=conn),
            patch.object(session, "_find_sdk_jsonl", return_value=None),
        ):
            session._record_session_close()

        row = conn.execute("SELECT * FROM memory_sessions WHERE channel_id='ch-close'").fetchone()
        assert row["closed_at"] is not None
        assert row["jsonl_path"] is None
        conn.close()

    def test_archives_jsonl_on_close(self, tmp_path):
        session = cc._LiveSession("ch-arch", Path("/tmp"), "")
        session.session_id = "sdk-session-2"

        conn = _make_conn()
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at) VALUES (?, ?, ?)",
            ("ch-arch:12345", "ch-arch", int(time.time())),
        )
        conn.commit()

        sdk_jsonl = tmp_path / "sdk.jsonl"
        sdk_jsonl.write_text('{"test": true}\n')
        archived = tmp_path / "archived.jsonl"

        with (
            patch("brain.infra.db.get_db", return_value=conn),
            patch.object(session, "_find_sdk_jsonl", return_value=sdk_jsonl),
            patch("brain.memory.ledger.archive_session_jsonl", return_value=archived),
        ):
            session._record_session_close()

        row = conn.execute("SELECT * FROM memory_sessions WHERE channel_id='ch-arch'").fetchone()
        assert row["jsonl_path"] == str(archived)
        conn.close()

    def test_does_not_raise_on_error(self):
        session = cc._LiveSession("ch-err", Path("/tmp"), "")
        session.session_id = "sid"
        with patch("brain.infra.db.get_db", side_effect=Exception("boom")):
            session._record_session_close()  # no exception


class TestRecordMessageCount:
    """_LiveSession._record_message_count: 递增 message_count。"""

    def test_increments(self):
        session = cc._LiveSession("ch-msg", Path("/tmp"), "")

        conn = _make_conn()
        conn.execute(
            "INSERT INTO memory_sessions (session_id, channel_id, opened_at) VALUES (?, ?, ?)",
            ("ch-msg:12345", "ch-msg", int(time.time())),
        )
        conn.commit()

        with patch("brain.infra.db.get_db", return_value=conn):
            session._record_message_count()
            session._record_message_count()

        row = conn.execute("SELECT message_count FROM memory_sessions WHERE channel_id='ch-msg'").fetchone()
        assert row["message_count"] == 2
        conn.close()

    def test_does_not_raise_on_error(self):
        session = cc._LiveSession("ch-err", Path("/tmp"), "")
        with patch("brain.infra.db.get_db", side_effect=Exception("boom")):
            session._record_message_count()  # no exception


class TestFindSdkJsonl:
    """_LiveSession._find_sdk_jsonl: SDK JSONL 路径定位。"""

    def test_returns_path_when_exists(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        session = cc._LiveSession("ch1", workspace, "")
        session.session_id = "sid-abc"

        cwd_resolved = str(workspace.resolve())
        project_hash = cwd_resolved.replace("/", "-").lstrip("-")
        sessions_dir = Path.home() / ".claude" / "projects" / project_hash / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        jsonl_file = sessions_dir / "sid-abc.jsonl"
        jsonl_file.write_text('{"data": true}\n')

        try:
            result = session._find_sdk_jsonl()
            assert result == jsonl_file
        finally:
            jsonl_file.unlink(missing_ok=True)
            # Clean up created dirs
            for d in [sessions_dir, sessions_dir.parent]:
                try:
                    d.rmdir()
                except OSError:
                    pass

    def test_returns_none_when_missing(self, tmp_path):
        session = cc._LiveSession("ch1", tmp_path / "workspace", "")
        session.session_id = "sid-missing"
        result = session._find_sdk_jsonl()
        assert result is None

    def test_returns_none_without_session_id(self, tmp_path):
        session = cc._LiveSession("ch1", tmp_path, "")
        session.session_id = None
        result = session._find_sdk_jsonl()
        assert result is None


class TestInitV2TablesCallsMemorySessions:
    """_init_v2_tables 应调用 _init_memory_sessions。"""

    def test_memory_sessions_created_via_init_v2(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _init_v2_tables(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_sessions)").fetchall()}
        assert "session_id" in cols
        conn.close()


class TestEnsureConnectedSessionTracking:
    """_ensure_connected 应在成功后调用 _record_session_open。"""

    async def test_calls_record_session_open(self):
        session = cc._LiveSession("ch-track", Path("/tmp"), "")
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()

        with (
            patch("brain.executor.cc.ClaudeSDKClient", return_value=mock_client),
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch.object(session, "_record_session_open") as mock_record,
        ):
            await session._ensure_connected()

        mock_record.assert_called_once()

    async def test_skips_when_memory_disabled(self):
        session = cc._LiveSession("ch-no-mem", Path("/tmp"), "")
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()

        with (
            patch("brain.executor.cc.ClaudeSDKClient", return_value=mock_client),
            patch("brain.executor.cc.MEMORY_ENABLED", False),
            patch.object(session, "_record_session_open") as mock_record,
        ):
            await session._ensure_connected()

        mock_record.assert_not_called()


class TestDisconnectSessionTracking:
    """_disconnect 应在 session_id 存在时调用 _record_session_close。"""

    async def test_calls_record_session_close(self):
        session = cc._LiveSession("ch-disc", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.client.disconnect = AsyncMock()
        session.session_id = "sid-1"

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch.object(session, "_record_session_close") as mock_record,
        ):
            await session._disconnect()

        mock_record.assert_called_once()

    async def test_skips_without_session_id(self):
        session = cc._LiveSession("ch-disc2", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()
        session.client.disconnect = AsyncMock()
        session.session_id = None

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch.object(session, "_record_session_close") as mock_record,
        ):
            await session._disconnect()

        mock_record.assert_not_called()


class TestQueryOnceMessageCount:
    """_query_once 应在成功后调用 _record_message_count。"""

    async def test_calls_record_message_count_on_success(self):
        session = cc._LiveSession("ch-qo", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()

        # Mock the SDK query + receive_response flow
        from claude_agent_sdk import ResultMessage

        mock_result = AsyncMock(spec=ResultMessage)
        mock_result.session_id = "sid-ok"
        mock_result.result = "done"
        mock_result.total_cost_usd = 0.01
        mock_result.duration_ms = 100
        mock_result.num_turns = 1

        async def mock_receive():
            yield mock_result

        session.client.query = AsyncMock()
        session.client.receive_response = mock_receive

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch.object(session, "_record_message_count") as mock_count,
        ):
            sid, text, meta = await session._query_once("hello")

        assert sid == "sid-ok"
        mock_count.assert_called_once()

    async def test_skips_when_no_session_id(self):
        session = cc._LiveSession("ch-no-sid", Path("/tmp"), "")
        session._connected = True
        session.client = AsyncMock()

        from claude_agent_sdk import ResultMessage

        mock_result = AsyncMock(spec=ResultMessage)
        mock_result.session_id = None
        mock_result.result = ""
        mock_result.total_cost_usd = 0
        mock_result.duration_ms = 0
        mock_result.num_turns = 0

        async def mock_receive():
            yield mock_result

        session.client.query = AsyncMock()
        session.client.receive_response = mock_receive

        with (
            patch("brain.executor.cc.MEMORY_ENABLED", True),
            patch.object(session, "_record_message_count") as mock_count,
        ):
            await session._query_once("hello")

        mock_count.assert_not_called()
