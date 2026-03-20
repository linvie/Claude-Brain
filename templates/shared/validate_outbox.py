#!/usr/bin/env python3
"""outbox.json 前置校验脚本。

CC 每次写入 outbox.json 后必须运行此脚本。
校验通过：退出码 0，输出 "PASS"。
校验失败：退出码 1，输出具体错误信息。
"""

import json
import sys
from pathlib import Path

VALID_STATUSES = {"TASK_DONE", "TASK_BLOCKED", "TASK_PROGRESS"}

OUTBOX_PATH = Path(__file__).parent / "outbox.json"
if not OUTBOX_PATH.exists():
    OUTBOX_PATH = Path("outbox.json")


def validate(path: Path) -> list[str]:
    """校验 outbox.json，返回错误列表（空 = 通过）。"""
    errors = []

    # 1. 文件存在
    if not path.exists():
        return ["outbox.json 不存在"]

    # 2. JSON 格式
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return ["outbox.json 为空"]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [f"JSON 解析失败: {e}"]

    # 3. 必须是 dict
    if not isinstance(data, dict):
        return ["outbox.json 根元素必须是对象（{{}}），不能是数组或其他类型"]

    # 4. status 字段
    status = data.get("status")
    if not status:
        errors.append("缺少 'status' 字段")
    elif status not in VALID_STATUSES:
        errors.append(f"status 值无效: '{status}'，合法值: {', '.join(sorted(VALID_STATUSES))}")

    # 5. summary 字段
    summary = data.get("summary")
    if not summary:
        errors.append("缺少 'summary' 字段或内容为空")
    elif not isinstance(summary, str):
        errors.append("'summary' 必须是字符串")

    # 6. status 特定字段
    if status == "TASK_BLOCKED":
        reason = data.get("reason")
        if not reason:
            errors.append("status=TASK_BLOCKED 时必须提供 'reason' 字段")
        elif not isinstance(reason, str):
            errors.append("'reason' 必须是字符串")

    if status == "TASK_PROGRESS":
        stage = data.get("stage")
        if not stage:
            errors.append("status=TASK_PROGRESS 时必须提供 'stage' 字段")
        elif not isinstance(stage, str):
            errors.append("'stage' 必须是字符串")

    # 7. artifacts（可选，但如果有必须是数组）
    if "artifacts" in data:
        artifacts = data["artifacts"]
        if not isinstance(artifacts, list):
            errors.append("'artifacts' 必须是数组")
        elif not all(isinstance(a, str) for a in artifacts):
            errors.append("'artifacts' 数组中的每个元素必须是字符串")

    return errors


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTBOX_PATH
    errors = validate(path)

    if errors:
        print("FAIL - outbox.json 校验失败:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
