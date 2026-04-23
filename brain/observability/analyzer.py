"""Rule-based trajectory analysis — no LLM, pure Python stdlib."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TrajectoryAnalysis:
    """Human-readable summary of a trajectory."""

    total_events: int = 0
    bad_lines: int = 0
    time_span: str = ""  # e.g. "12m 34s"
    first_ts: str = ""
    last_ts: str = ""
    tool_histogram: dict[str, int] = field(default_factory=dict)
    failure_points: list[dict] = field(default_factory=list)
    bash_top10: list[tuple[str, int]] = field(default_factory=list)
    has_stop: bool = False


def _parse_ts(ts_str: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        return "0s"
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def analyze_trajectory(events: list[dict], bad_lines: int = 0) -> TrajectoryAnalysis:
    """Analyze a list of trajectory events and return a structured summary."""
    analysis = TrajectoryAnalysis(total_events=len(events), bad_lines=bad_lines)

    if not events:
        return analysis

    # Tool histogram
    tool_counter: Counter[str] = Counter()
    bash_commands: Counter[str] = Counter()
    timestamps: list[datetime] = []

    for ev in events:
        # Tool counting
        tool = ev.get("tool", "") or ev.get("event", "unknown")
        tool_counter[tool] += 1

        # Stop detection
        event_name = ev.get("event", "")
        if event_name == "Stop" or event_name == "stop":
            analysis.has_stop = True

        # Failure detection (exit_code != 0)
        exit_code = ev.get("exit_code")
        if exit_code is not None and exit_code != 0:
            analysis.failure_points.append({
                "ts": ev.get("ts", ""),
                "tool": tool,
                "exit_code": exit_code,
                "input_preview": (ev.get("input_preview", "") or "")[:80],
            })

        # Bash command aggregation (first 50 chars of input_preview)
        if tool == "Bash" or tool == "bash":
            preview = (ev.get("input_preview", "") or "")[:50]
            if preview:
                bash_commands[preview] += 1

        # Timestamps
        ts = _parse_ts(ev.get("ts", ""))
        if ts:
            timestamps.append(ts)

    analysis.tool_histogram = dict(tool_counter.most_common())

    # Top 10 bash commands
    analysis.bash_top10 = bash_commands.most_common(10)

    # Time span
    if timestamps:
        first = min(timestamps)
        last = max(timestamps)
        analysis.first_ts = first.strftime("%Y-%m-%dT%H:%M:%SZ")
        analysis.last_ts = last.strftime("%Y-%m-%dT%H:%M:%SZ")
        analysis.time_span = _format_duration((last - first).total_seconds())

    return analysis


def format_summary(analysis: TrajectoryAnalysis) -> str:
    """Format a TrajectoryAnalysis into a human-readable string."""
    lines: list[str] = []
    lines.append(f"Events: {analysis.total_events}")
    if analysis.bad_lines:
        lines.append(f"Bad lines (skipped): {analysis.bad_lines}")
    if analysis.time_span:
        lines.append(f"Time span: {analysis.time_span} ({analysis.first_ts} → {analysis.last_ts})")
    lines.append(f"Stop event: {'yes' if analysis.has_stop else 'no'}")

    # Tool histogram
    lines.append("")
    lines.append("Tool usage:")
    for tool, count in sorted(analysis.tool_histogram.items(), key=lambda x: -x[1]):
        lines.append(f"  {tool:<25} {count:>4}")

    # Failure points
    if analysis.failure_points:
        lines.append("")
        lines.append(f"Failures ({len(analysis.failure_points)}):")
        for fp in analysis.failure_points:
            lines.append(f"  [{fp['ts']}] {fp['tool']} exit={fp['exit_code']}  {fp['input_preview']}")
    else:
        lines.append("")
        lines.append("Failures: none")

    # Bash top 10
    if analysis.bash_top10:
        lines.append("")
        lines.append("Top Bash commands:")
        for cmd, count in analysis.bash_top10:
            lines.append(f"  {count:>3}x  {cmd}")

    return "\n".join(lines)
