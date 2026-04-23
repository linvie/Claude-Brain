"""Tests for brain.observability — trajectory reader and analyzer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.observability.analyzer import (
    TrajectoryAnalysis,
    analyze_trajectory,
    format_summary,
)
from brain.observability.reader import ReadResult, iter_trajectory, read_trajectory

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

SAMPLE_EVENTS = [
    {
        "ts": "2026-04-23T10:00:00Z",
        "task_id": "abc12345",
        "event": "PostToolUse",
        "tool": "Read",
        "cwd": "/tmp",
        "input_preview": '{"file_path": "/tmp/foo.py"}',
    },
    {
        "ts": "2026-04-23T10:01:00Z",
        "task_id": "abc12345",
        "event": "PostToolUse",
        "tool": "Bash",
        "cwd": "/tmp",
        "input_preview": "uv run pytest tests/ -x -q",
        "exit_code": 0,
        "duration_ms": 3200,
    },
    {
        "ts": "2026-04-23T10:02:30Z",
        "task_id": "abc12345",
        "event": "PostToolUse",
        "tool": "Bash",
        "cwd": "/tmp",
        "input_preview": "git status",
        "exit_code": 1,
        "duration_ms": 120,
    },
    {
        "ts": "2026-04-23T10:03:00Z",
        "task_id": "abc12345",
        "event": "PostToolUse",
        "tool": "Edit",
        "cwd": "/tmp",
        "input_preview": '{"file_path": "/tmp/foo.py", "old_string": "x", "new_string": "y"}',
    },
    {
        "ts": "2026-04-23T10:05:00Z",
        "task_id": "abc12345",
        "event": "Stop",
        "tool": "",
        "cwd": "/tmp",
        "input_preview": "",
    },
]


@pytest.fixture()
def jsonl_file(tmp_path: Path) -> Path:
    """Write sample events to a temporary JSONL file."""
    f = tmp_path / "abc12345.jsonl"
    with open(f, "w") as fh:
        for ev in SAMPLE_EVENTS:
            fh.write(json.dumps(ev) + "\n")
    return f


@pytest.fixture()
def jsonl_with_bad_lines(tmp_path: Path) -> Path:
    """JSONL file with some bad lines mixed in."""
    f = tmp_path / "bad.jsonl"
    with open(f, "w") as fh:
        fh.write(json.dumps(SAMPLE_EVENTS[0]) + "\n")
        fh.write("NOT VALID JSON\n")
        fh.write("[1, 2, 3]\n")  # valid JSON but not a dict
        fh.write(json.dumps(SAMPLE_EVENTS[1]) + "\n")
        fh.write("\n")  # empty line — should be skipped silently
        fh.write("{broken json\n")
    return f


# ---------------------------------------------------------------------------
# Reader tests
# ---------------------------------------------------------------------------


class TestReader:
    def test_read_valid_jsonl(self, jsonl_file: Path):
        result = read_trajectory(jsonl_file)
        assert isinstance(result, ReadResult)
        assert len(result.events) == 5
        assert result.bad_lines == 0

    def test_read_nonexistent_file(self, tmp_path: Path):
        result = read_trajectory(tmp_path / "nope.jsonl")
        assert result.events == []
        assert result.bad_lines == 0

    def test_read_bad_lines_counted(self, jsonl_with_bad_lines: Path):
        result = read_trajectory(jsonl_with_bad_lines)
        assert len(result.events) == 2  # only 2 valid dict events
        assert result.bad_lines == 3  # "NOT VALID JSON", [1,2,3], "{broken json"

    def test_iter_trajectory(self, jsonl_file: Path):
        events = list(iter_trajectory(jsonl_file))
        assert len(events) == 5
        assert events[0]["tool"] == "Read"

    def test_iter_nonexistent(self, tmp_path: Path):
        events = list(iter_trajectory(tmp_path / "nope.jsonl"))
        assert events == []

    def test_read_accepts_string_path(self, jsonl_file: Path):
        result = read_trajectory(str(jsonl_file))
        assert len(result.events) == 5


# ---------------------------------------------------------------------------
# Analyzer tests
# ---------------------------------------------------------------------------


class TestAnalyzer:
    def test_empty_events(self):
        analysis = analyze_trajectory([])
        assert analysis.total_events == 0
        assert analysis.tool_histogram == {}
        assert analysis.has_stop is False

    def test_tool_histogram(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS)
        assert analysis.tool_histogram["Bash"] == 2
        assert analysis.tool_histogram["Read"] == 1
        assert analysis.tool_histogram["Edit"] == 1

    def test_failure_points(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS)
        assert len(analysis.failure_points) == 1
        fp = analysis.failure_points[0]
        assert fp["exit_code"] == 1
        assert fp["tool"] == "Bash"
        assert "git status" in fp["input_preview"]

    def test_stop_detection(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS)
        assert analysis.has_stop is True

    def test_no_stop(self):
        events_no_stop = [e for e in SAMPLE_EVENTS if e["event"] != "Stop"]
        analysis = analyze_trajectory(events_no_stop)
        assert analysis.has_stop is False

    def test_time_span(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS)
        assert analysis.time_span == "5m 0s"
        assert analysis.first_ts == "2026-04-23T10:00:00Z"
        assert analysis.last_ts == "2026-04-23T10:05:00Z"

    def test_bash_top10(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS)
        # Two unique bash commands
        assert len(analysis.bash_top10) == 2
        cmds = {cmd for cmd, _ in analysis.bash_top10}
        assert "git status" in cmds
        assert "uv run pytest tests/ -x -q" in cmds

    def test_bad_lines_passed_through(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS, bad_lines=3)
        assert analysis.bad_lines == 3

    def test_exit_code_zero_not_failure(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS)
        for fp in analysis.failure_points:
            assert fp["exit_code"] != 0


class TestFormatSummary:
    def test_format_contains_key_sections(self):
        analysis = analyze_trajectory(SAMPLE_EVENTS, bad_lines=1)
        text = format_summary(analysis)
        assert "Events: 5" in text
        assert "Bad lines" in text
        assert "Tool usage:" in text
        assert "Bash" in text
        assert "Failures (1):" in text
        assert "git status" in text
        assert "Top Bash commands:" in text
        assert "Stop event: yes" in text

    def test_format_no_failures(self):
        events = [e for e in SAMPLE_EVENTS if e.get("exit_code", 0) == 0 or "exit_code" not in e]
        analysis = analyze_trajectory(events)
        text = format_summary(analysis)
        assert "Failures: none" in text

    def test_format_empty(self):
        analysis = analyze_trajectory([])
        text = format_summary(analysis)
        assert "Events: 0" in text
