"""交互式配置向导 — 引导用户完成 CCBrain 初始化配置。"""

import shutil
from pathlib import Path

import yaml

from brain.config import CONFIG_EXAMPLE_PATH, CONFIG_PATH, DATA_DIR


def _ask(prompt: str, default: str = "") -> str:
    if default:
        raw = input(f"  {prompt} [{default}]: ").strip()
        return raw or default
    return input(f"  {prompt}: ").strip()


def _confirm(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _ensure_data_dir():
    """确保 ~/.ccbrain/ 目录存在。"""
    if DATA_DIR.exists():
        return
    DATA_DIR.mkdir(parents=True)
    print(f"  创建数据目录: {DATA_DIR}")


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        print(f"  检测到已有配置: {CONFIG_PATH}")
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    else:
        print(f"  从模板创建配置文件...")
        shutil.copy(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}


def _save_config(config: dict):
    lines = CONFIG_PATH.read_text().splitlines()
    new_lines = _apply_config(lines, config)
    CONFIG_PATH.write_text("\n".join(new_lines) + "\n")


def _apply_config(lines: list[str], config: dict) -> list[str]:
    flat: dict[str, str] = {}
    for section, values in config.items():
        if isinstance(values, dict):
            for key, val in values.items():
                flat[f"{section}.{key}"] = val
        else:
            flat[section] = values

    result = []
    current_section = ""
    for line in lines:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and ":" in stripped and not line.startswith(" "):
            sec_name = stripped.split(":")[0].strip()
            if sec_name in config and isinstance(config.get(sec_name), dict):
                current_section = sec_name

        if current_section and stripped and not stripped.startswith("#") and ":" in stripped and line.startswith(" "):
            key_name = stripped.split(":")[0].strip()
            flat_key = f"{current_section}.{key_name}"
            if flat_key in flat:
                val = flat[flat_key]
                indent = line[: len(line) - len(line.lstrip())]
                comment = ""
                parts = line.split("#", 1)
                if len(parts) > 1 and not line.strip().startswith("#"):
                    comment = f"  # {parts[1].strip()}"
                result.append(f"{indent}{key_name}: {_yaml_val(val)}{comment}")
                continue

        result.append(line)
    return result


def _yaml_val(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        if not val:
            return '""'
        return f'"{val}"'
    return str(val)


def _setup_notion(config: dict) -> bool:
    print("\n── Notion 配置（v1 异步任务流）──")
    print("  Notion 任务流: 在 Notion 写需求 → Brain 自动调度 CC 执行 → 结果写回 Notion")
    print()

    current_token = config.get("notion", {}).get("token", "")
    if current_token:
        print(f"  当前 Token: {current_token[:12]}...")

    if not _confirm("启用 Notion 任务流?", default=bool(current_token)):
        config.setdefault("notion", {})["token"] = ""
        return False

    token = _ask("Notion Integration Token (ntn_开头)", default=current_token)
    if not token:
        print("  ⚠ 未填写 Token，Notion 未启用")
        return False

    config.setdefault("notion", {})["token"] = token

    current_project_db = config.get("notion", {}).get("project_db_id", "")
    current_task_db = config.get("notion", {}).get("task_db_id", "")

    if current_project_db and current_task_db:
        print(f"  已有数据库 ID（Project: {current_project_db[:8]}..., Task: {current_task_db[:8]}...）")
        if not _confirm("重新配置数据库 ID?"):
            return True

    print()
    print("  数据库 ID 可通过以下方式获取：")
    print("    方式 1: 在 Claude Code 中运行 /brain-init 自动创建")
    print("    方式 2: 手动在 Notion 创建数据库，从 URL 获取 ID")
    print()

    project_db = _ask("Project 数据库 ID (留空跳过)", default=current_project_db)
    task_db = _ask("Task 数据库 ID (留空跳过)", default=current_task_db)

    if project_db:
        config["notion"]["project_db_id"] = project_db
    if task_db:
        config["notion"]["task_db_id"] = task_db

    if not project_db or not task_db:
        print("  提示: 稍后在 Claude Code 中运行 /brain-init 可自动创建数据库")

    return True


def _setup_feishu(config: dict) -> bool:
    print("\n── 飞书配置（v2 实时对话流）──")
    print("  飞书对话流: 在飞书发消息 → Brain 接收 → CC 执行 → 结果回飞书")
    print()

    current_enabled = config.get("feishu", {}).get("enabled", False)
    current_app_id = config.get("feishu", {}).get("app_id", "")

    if current_app_id:
        print(f"  当前 App ID: {current_app_id}")

    if not _confirm("启用飞书对话?", default=current_enabled):
        config.setdefault("feishu", {})["enabled"] = False
        return False

    print()
    print("  需要在飞书开发者后台创建应用：")
    print("    1. 前往 https://open.feishu.cn/app → 创建企业自建应用")
    print("    2. 启用「机器人」能力")
    print("    3. 订阅事件 im.message.receive_v1")
    print("    4. 接收方式选择「使用长连接接收事件」")
    print("    5. 添加权限: im:message, im:message:send_as_bot")
    print("    6. 发布应用版本")
    print()

    app_id = _ask("App ID (cli_开头)", default=current_app_id)
    if not app_id:
        print("  ⚠ 未填写 App ID，飞书未启用")
        config.setdefault("feishu", {})["enabled"] = False
        return False

    current_secret = config.get("feishu", {}).get("app_secret", "")
    app_secret = _ask("App Secret", default=current_secret if current_secret else "")
    if not app_secret:
        print("  ⚠ 未填写 App Secret，飞书未启用")
        config.setdefault("feishu", {})["enabled"] = False
        return False

    feishu_cfg = config.setdefault("feishu", {})
    feishu_cfg["enabled"] = True
    feishu_cfg["app_id"] = app_id
    feishu_cfg["app_secret"] = app_secret

    return True


def main():
    print()
    print("  ╔══════════════════════════════════╗")
    print("  ║     CCBrain 配置向导              ║")
    print("  ╚══════════════════════════════════╝")

    _ensure_data_dir()
    config = _load_config()

    notion_enabled = _setup_notion(config)
    feishu_enabled = _setup_feishu(config)

    _save_config(config)

    print()
    print("  ── 配置摘要 ──")
    print(f"  数据目录:       {DATA_DIR}")
    print(f"  Notion 任务流:  {'已启用' if notion_enabled else '未启用'}")
    print(f"  飞书对话流:     {'已启用' if feishu_enabled else '未启用'}")
    print()

    if not notion_enabled and not feishu_enabled:
        print("  ⚠ 未启用任何事件源，Brain 启动后将空转等待。")
        print("  重新运行 ccbrain init 进行配置。")
    else:
        print("  运行 ccbrain start 启动服务")
        if notion_enabled and not config.get("notion", {}).get("project_db_id"):
            print("  提示: Notion 数据库未配置，在 Claude Code 中运行 /brain-init 自动创建")

    print()


if __name__ == "__main__":
    main()
