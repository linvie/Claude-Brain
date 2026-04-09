"""交互式配置向导 — 引导用户完成 Brain 初始化配置。"""

import shutil
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
CONFIG_EXAMPLE_PATH = BASE_DIR / "config.example.yaml"


def _ask(prompt: str, default: str = "") -> str:
    """带默认值的输入提示。"""
    if default:
        raw = input(f"  {prompt} [{default}]: ").strip()
        return raw or default
    return input(f"  {prompt}: ").strip()


def _confirm(prompt: str, default: bool = False) -> bool:
    """y/n 确认。"""
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _load_config() -> dict:
    """加载或创建 config.yaml。"""
    if CONFIG_PATH.exists():
        print(f"\n  检测到已有配置: {CONFIG_PATH}")
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    else:
        print(f"\n  从模板创建配置文件...")
        shutil.copy(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}


def _save_config(config: dict):
    """更新 config.yaml 中的值，保留注释和结构。

    逐行替换已知 key 的值，不破坏文件格式。
    对于嵌套 key（如 notion.token），在父 key 下找到子 key 行并替换。
    """
    lines = CONFIG_PATH.read_text().splitlines()
    new_lines = _apply_config(lines, config)
    CONFIG_PATH.write_text("\n".join(new_lines) + "\n")


def _apply_config(lines: list[str], config: dict) -> list[str]:
    """逐行更新配置值。"""
    # 构建 flat key → value 映射
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
        # 检测顶层 section（无缩进，以 key: 结尾）
        if stripped and not stripped.startswith("#") and ":" in stripped and not line.startswith(" "):
            sec_name = stripped.split(":")[0].strip()
            if sec_name in config and isinstance(config.get(sec_name), dict):
                current_section = sec_name

        # 检测子 key（有缩进）
        if current_section and stripped and not stripped.startswith("#") and ":" in stripped and line.startswith(" "):
            key_name = stripped.split(":")[0].strip()
            flat_key = f"{current_section}.{key_name}"
            if flat_key in flat:
                val = flat[flat_key]
                indent = line[: len(line) - len(line.lstrip())]
                comment = ""
                # 保留行尾注释
                parts = line.split("#", 1)
                if len(parts) > 1 and not line.strip().startswith("#"):
                    comment = f"  # {parts[1].strip()}"
                result.append(f"{indent}{key_name}: {_yaml_val(val)}{comment}")
                continue

        result.append(line)
    return result


def _yaml_val(val) -> str:
    """将 Python 值转为 YAML 字面量。"""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        if not val:
            return '""'
        # 含特殊字符时用引号
        if any(c in val for c in ":#{}[]|>&*!%@`"):
            return f'"{val}"'
        return f'"{val}"'
    return str(val)


def _setup_notion(config: dict) -> bool:
    """配置 Notion（v1 任务流）。"""
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

    # 数据库 ID
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
    """配置飞书（v2 对话流）。"""
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


def _setup_general(config: dict):
    """配置通用项。"""
    print("\n── 通用配置 ──")

    workspace_base = config.get("workspace", {}).get("base_dir", "~/brain-workspaces")
    new_base = _ask("Workspace 根目录", default=workspace_base)
    config.setdefault("workspace", {})["base_dir"] = new_base


def main():
    print()
    print("  ╔══════════════════════════════════╗")
    print("  ║   Claude Brain 配置向导           ║")
    print("  ╚══════════════════════════════════╝")

    config = _load_config()

    _setup_general(config)
    notion_enabled = _setup_notion(config)
    feishu_enabled = _setup_feishu(config)

    # 保存
    _save_config(config)

    # 摘要
    print()
    print("  ── 配置摘要 ──")
    print(f"  Notion 任务流:  {'已启用' if notion_enabled else '未启用'}")
    print(f"  飞书对话流:     {'已启用' if feishu_enabled else '未启用'}")
    print(f"  配置文件:       {CONFIG_PATH}")
    print()

    if not notion_enabled and not feishu_enabled:
        print("  ⚠ 未启用任何事件源，Brain 启动后将空转等待。")
        print("  重新运行 ./brain.sh init 进行配置。")
    else:
        print("  运行 ./brain.sh start 启动服务")
        if notion_enabled and not config.get("notion", {}).get("project_db_id"):
            print("  提示: Notion 数据库未配置，在 Claude Code 中运行 /brain-init 自动创建")

    print()


if __name__ == "__main__":
    main()
