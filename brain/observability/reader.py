"""Read trajectory JSONL files, yielding event dicts.

Bad lines are silently skipped and counted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class ReadResult:
    """Container for trajectory read results."""

    events: list[dict] = field(default_factory=list)
    bad_lines: int = 0


def read_trajectory(path: Path | str) -> ReadResult:
    """Read a trajectory JSONL file, returning events and bad-line count."""
    path = Path(path)
    result = ReadResult()
    if not path.exists():
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    result.bad_lines += 1
                    continue
                result.events.append(event)
            except (json.JSONDecodeError, ValueError):
                result.bad_lines += 1
    return result


def iter_trajectory(path: Path | str) -> Iterator[dict]:
    """Yield events one by one from a trajectory JSONL file (skips bad lines)."""
    path = Path(path)
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    yield event
            except (json.JSONDecodeError, ValueError):
                continue
