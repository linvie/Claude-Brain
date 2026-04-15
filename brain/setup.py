"""交互式配置向导 — 引导用户完成 CCBrain 初始化配置。"""

import json
import shutil
from pathlib import Path

import yaml

from brain.config import CONFIG_EXAMPLE_PATH, CONFIG_PATH, DATA_DIR


def _ask(prompt: str, default: str = "") -> str:  # pragma: no cover
    if default:
        raw = input(f"  {prompt} [{default}]: ").strip()
        return raw or default
    return input(f"  {prompt}: ").strip()


def _confirm(prompt: str, default: bool = False) -> bool:  # pragma: no cover
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _ensure_data_dir():  # pragma: no cover
    """确保 ~/.ccbrain/ 目录存在。"""
    if DATA_DIR.exists():
        return
    DATA_DIR.mkdir(parents=True)
    print(f"  创建数据目录: {DATA_DIR}")


def _load_config() -> dict:  # pragma: no cover
    if CONFIG_PATH.exists():
        print(f"  检测到已有配置: {CONFIG_PATH}")
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    else:
        print("  从模板创建配置文件...")
        shutil.copy(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}


def _save_config(config: dict):  # pragma: no cover
    lines = CONFIG_PATH.read_text().splitlines()
    new_lines = _apply_config(lines, config)
    CONFIG_PATH.write_text("\n".join(new_lines) + "\n")


def _apply_config(lines: list[str], config: dict) -> list[str]:
    # 只处理 section.key: scalar_value（跳过深层嵌套如 roles）
    flat: dict[str, str] = {}
    for section, values in config.items():
        if isinstance(values, dict):
            for key, val in values.items():
                if not isinstance(val, (dict, list)):
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


def _setup_notion(config: dict) -> bool:  # pragma: no cover
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

    # 配置全局 Notion MCP（飞书对话中使用）
    _setup_notion_mcp(token)

    return True


def _setup_notion_mcp(token: str):  # pragma: no cover
    """配置全局 Notion MCP server，使 CC 能在飞书对话中操作 Notion。"""
    import subprocess

    # 检查是否已配置
    result = subprocess.run(
        ["claude", "mcp", "list"],
        capture_output=True, text=True, timeout=10,
    )
    if "notion" in result.stdout.lower() and "notion-mcp-server" in result.stdout:
        print("  ✓ Notion MCP 已配置")
        return

    print("  配置 Notion MCP（飞书对话中可操作 Notion）...")
    # 先尝试用 OPENAPI_MCP_HEADERS（官方推荐），失败则用直接写入 JSON 的方式
    headers_json = json.dumps({
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
    })
    try:
        subprocess.run(
            [
                "claude", "mcp", "add",
                "--scope", "user",
                "--transport", "stdio",
                "--env", f"OPENAPI_MCP_HEADERS={headers_json}",
                "notion",
                "--",
                "npx", "-y", "@notionhq/notion-mcp-server",
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
        print("  ✓ Notion MCP 配置完成")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # claude mcp add 失败时，直接写入 ~/.claude.json
        try:
            _write_notion_mcp_config(token, headers_json)
            print("  ✓ Notion MCP 配置完成（直接写入 ~/.claude.json）")
        except Exception as e:
            print(f"  ⚠ Notion MCP 配置失败: {e}")
            print("  手动运行: claude mcp add --scope user notion -- npx -y @notionhq/notion-mcp-server")


def _write_notion_mcp_config(token: str, headers_json: str):
    """直接写入 ~/.claude.json 配置 Notion MCP（claude mcp add 的 fallback）。"""
    claude_json_path = Path.home() / ".claude.json"
    if claude_json_path.exists():
        data = json.loads(claude_json_path.read_text())
    else:
        data = {}

    data.setdefault("mcpServers", {})
    data["mcpServers"]["notion"] = {
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {
            "OPENAPI_MCP_HEADERS": headers_json,
        },
    }
    claude_json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _setup_feishu(config: dict) -> bool:  # pragma: no cover
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

    # 平台选择
    current_platform = config.get("feishu", {}).get("platform", "feishu")
    print()
    print("  请选择平台:")
    print("    1) 飞书（open.feishu.cn）")
    print("    2) Lark 国际版（open.larksuite.com）")
    default_choice = "2" if current_platform == "lark" else "1"
    choice = _ask("选择 [1/2]", default=default_choice)
    platform = "lark" if choice.strip() == "2" else "feishu"
    config.setdefault("feishu", {})["platform"] = platform

    console_url = (
        "https://open.larksuite.com/app" if platform == "lark"
        else "https://open.feishu.cn/app"
    )
    print()
    print("  需要在开发者后台创建应用：")
    print(f"    1. 前往 {console_url} → 创建企业自建应用")
    print("    2. 启用「机器人」能力")
    print("    3. 事件订阅（接收方式选「使用长连接接收事件」）：")
    print("       - im.message.receive_v1          （接收消息）")
    print("    4. 添加权限 scope：")
    print("       - im:message                     （读取消息）")
    print("       - im:message:send_as_bot         （发送消息）")
    print("       - im:chat:readonly               （读取群信息，群聊必需）")
    print("       - im:message:reaction            （表情回复，Typing 指示器）")
    print("    5. 发布应用版本")
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
    feishu_cfg["platform"] = platform
    feishu_cfg["app_id"] = app_id
    feishu_cfg["app_secret"] = app_secret

    return True


def _setup_lark_cli() -> bool:  # pragma: no cover
    """配置飞书 CLI（lark-cli），为 CC 提供飞书工具能力。"""
    print("\n── 飞书 CLI 配置（CC 工具扩展）──")
    print("  安装后 CC 可直接操作飞书：发消息、查日历、管理文档、查任务等")
    print()

    if not _confirm("安装飞书 CLI 工具?"):
        return False

    import shutil
    import subprocess

    # 检查 npm
    if not shutil.which("npm"):
        print("  ⚠ 未检测到 npm，请先安装 Node.js")
        print("    brew install node  或  https://nodejs.org/")
        return False

    # 检查 lark-cli 是否已安装
    if shutil.which("lark-cli"):
        print("  ✓ lark-cli 已安装")
    else:
        print("  正在安装 lark-cli...")
        r = subprocess.run(
            ["npm", "install", "-g", "@larksuite/cli"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ⚠ 安装失败: {r.stderr[:200]}")
            return False
        print("  ✓ lark-cli 安装完成")

    # 安装 Claude Code skills（交互式，输出到终端）
    print("  正在安装 CC skills（飞书工具集）...")
    print()
    try:
        r = subprocess.run(
            ["npx", "skills", "add", "larksuite/cli", "-y", "-g"],
            timeout=180,
        )
        if r.returncode == 0:
            print()
            print("  ✓ CC skills 安装完成")
        else:
            print()
            print("  ⚠ skills 安装失败，请手动执行：")
            print("    npx skills add larksuite/cli -y -g")
    except subprocess.TimeoutExpired:
        print()
        print("  ⚠ skills 安装超时，请手动执行：")
        print("    npx skills add larksuite/cli -y -g")

    # 配置应用
    print()
    print("  接下来需要配置飞书应用并登录授权。")
    print("  这会打开浏览器完成认证。")
    print()

    if _confirm("现在配置飞书应用?", default=True):
        print("  正在打开飞书应用配置...")
        subprocess.run(["lark-cli", "config", "init"])
        print()
        print("  正在进行登录授权...")
        subprocess.run(["lark-cli", "auth", "login", "--recommend"])

        # 验证
        r = subprocess.run(
            ["lark-cli", "auth", "status"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("  ✓ 授权成功")
            print(f"  {r.stdout.strip()[:200]}")
        else:
            print("  ⚠ 授权状态未确认，可稍后运行: lark-cli auth login --recommend")
    else:
        print("  稍后手动运行：")
        print("    lark-cli config init")
        print("    lark-cli auth login --recommend")

    return True


def main():  # pragma: no cover
    print()
    print("  ╔══════════════════════════════════╗")
    print("  ║     CCBrain 配置向导              ║")
    print("  ╚══════════════════════════════════╝")

    _ensure_data_dir()
    config = _load_config()

    notion_enabled = _setup_notion(config)
    feishu_enabled = _setup_feishu(config)
    lark_cli = _setup_lark_cli() if feishu_enabled else False

    _save_config(config)

    print()
    print("  ── 配置摘要 ──")
    print(f"  数据目录:       {DATA_DIR}")
    print(f"  Notion 任务流:  {'已启用' if notion_enabled else '未启用'}")
    print(f"  飞书对话流:     {'已启用' if feishu_enabled else '未启用'}")
    print(f"  飞书 CLI:       {'已配置' if lark_cli else '未配置'}")
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
