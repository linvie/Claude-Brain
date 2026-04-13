"""CCBrain CLI — 全局命令行入口，任何目录下均可运行。

数据目录: ~/.ccbrain/（config、state.db、logs、workspaces）
源码目录: 通过 pip/uv 安装，与数据分离

用法：
    ccbrain init          交互式配置向导
    ccbrain run           前台运行（调试用）
    ccbrain install       注册 launchd 服务并启动
    ccbrain uninstall     卸载 launchd 服务
    ccbrain start         启动服务
    ccbrain stop          停止服务
    ccbrain restart       重启服务
    ccbrain status        查看运行状态
    ccbrain logs [name]   tail -f 日志
"""

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from shutil import which

from brain.config import DATA_DIR, LOG_DIR, SRC_DIR

LABEL = "com.linvie.ccbrain"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


def _uv() -> str:  # pragma: no cover
    return which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:  # pragma: no cover
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _is_loaded() -> bool:  # pragma: no cover
    return _run(f"launchctl print {DOMAIN}/{LABEL}", check=False).returncode == 0


def _generate_plist() -> str:
    uv = _uv()
    # 继承当前 shell 的 PATH，确保 launchd 环境能找到 node/npx/lark-cli 等
    shell_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{shell_path}</string>
        <key>CLAUDE_AUTOCOMPACT_PCT_OVERRIDE</key>
        <string>70</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>{uv}</string>
        <string>run</string>
        <string>--directory</string>
        <string>{SRC_DIR}</string>
        <string>python</string>
        <string>-m</string>
        <string>brain</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{SRC_DIR}</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_DIR}/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{LOG_DIR}/launchd.stderr.log</string>
</dict>
</plist>"""


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_init():  # pragma: no cover
    from brain.setup import main as setup_main
    setup_main()


def cmd_run():  # pragma: no cover
    if not DATA_DIR.exists():
        print(f"数据目录不存在: {DATA_DIR}")
        print("请先运行 ccbrain init")
        sys.exit(1)
    from brain.main import main
    asyncio.run(main())


def cmd_install():  # pragma: no cover
    if not DATA_DIR.exists():
        print("请先运行 ccbrain init")
        sys.exit(1)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_generate_plist())
    # 如果已 loaded，先卸载再重新加载（覆盖安装）
    if _is_loaded():
        _run(f"launchctl bootout {DOMAIN}/{LABEL}", check=False)
        time.sleep(1)
    _run(f"launchctl enable {DOMAIN}/{LABEL}", check=False)
    r = _run(f"launchctl bootstrap {DOMAIN} {PLIST_PATH}", check=False)
    if r.returncode != 0:
        print(f"安装失败: {r.stderr.strip()}")
        sys.exit(1)
    print("✓ 服务已安装并启动")
    print(f"  plist: {PLIST_PATH}")
    print(f"  数据:  {DATA_DIR}/")


def cmd_uninstall():  # pragma: no cover
    if not _is_loaded():
        print("服务未安装")
        if PLIST_PATH.exists():
            PLIST_PATH.unlink()
            print("已清理残留 plist")
        return
    _run(f"launchctl bootout {DOMAIN}/{LABEL}", check=False)
    PLIST_PATH.unlink(missing_ok=True)
    print("✓ 服务已卸载")


def _has_running_pid() -> bool:  # pragma: no cover
    r = _run(f"launchctl print {DOMAIN}/{LABEL}", check=False)
    return "pid =" in r.stdout and r.returncode == 0


def cmd_start():  # pragma: no cover
    if not PLIST_PATH.exists():
        print("服务未安装，请先运行 ccbrain install")
        sys.exit(1)
    if _has_running_pid():
        print("服务已在运行")
        return
    # bootstrap 加载 plist 并启动（KeepAlive 生效）
    _run(f"launchctl bootstrap {DOMAIN} {PLIST_PATH}", check=False)
    print("✓ 服务已启动")


def cmd_stop():  # pragma: no cover
    if not _is_loaded():
        print("服务未在运行")
        return
    # bootout 彻底卸载服务：终止进程 + 移除 KeepAlive，不会自动重启
    # plist 文件保留，start 时重新 bootstrap
    _run(f"launchctl bootout {DOMAIN}/{LABEL}", check=False)
    print("✓ 服务已停止")


def cmd_restart():  # pragma: no cover
    cmd_stop()
    time.sleep(1)
    cmd_start()


def cmd_status():  # pragma: no cover
    if not _is_loaded():
        print("服务未安装")
        return
    r = _run(f"launchctl print {DOMAIN}/{LABEL}", check=False)
    pid_match = re.search(r"pid = (\d+)", r.stdout)
    if pid_match:
        pid = pid_match.group(1)
        elapsed_r = _run(f"ps -o etime= -p {pid}", check=False)
        elapsed = elapsed_r.stdout.strip() or "unknown"
        print(f"● running (PID {pid}, uptime {elapsed})")
    else:
        print("● stopped")


def cmd_logs(name: str = "brain"):  # pragma: no cover
    log_file = LOG_DIR / f"{name}.log"
    if not log_file.exists():
        print(f"日志文件不存在: {log_file}")
        print("可用: brain, scheduler, cc, notion, feishu, session, memory, launchd.stdout, launchd.stderr")
        sys.exit(1)
    os.execvp("tail", ["tail", "-f", str(log_file)])


def usage():  # pragma: no cover
    print(f"""\
CCBrain — Claude Code Brain Daemon

用法: ccbrain <command>

命令:
  init              交互式配置向导（首次使用）
  config <sub>      配置管理（show/edit/feishu/notion/lark-cli）
  run               前台运行（调试用，Ctrl+C 停止）
  install           注册 launchd 服务并启动
  uninstall         卸载服务（不删除数据）
  start             启动服务
  stop              停止服务（优雅关闭）
  restart           重启服务
  status            查看运行状态
  logs [name]       查看日志（brain, scheduler, cc, notion, feishu, session, memory）

数据目录: {DATA_DIR}""")


def cmd_config(args: list[str]):  # pragma: no cover
    """配置管理：查看、修改配置项，安装扩展工具。"""
    sub = args[0] if args else ""

    if sub == "edit":
        # 直接打开编辑器
        import os as _os
        editor = _os.environ.get("EDITOR", "vim")
        from brain.config import CONFIG_PATH
        if not CONFIG_PATH.exists():
            print("配置文件不存在，请先运行 ccbrain init")
            sys.exit(1)
        _os.execvp(editor, [editor, str(CONFIG_PATH)])

    elif sub == "path":
        from brain.config import CONFIG_PATH
        print(CONFIG_PATH)

    elif sub == "show":
        from brain.config import CONFIG_PATH
        if CONFIG_PATH.exists():
            print(CONFIG_PATH.read_text())
        else:
            print("配置文件不存在，请先运行 ccbrain init")

    elif sub == "lark-cli":
        from brain.setup import _setup_lark_cli
        _setup_lark_cli()

    elif sub == "notion":
        from brain.setup import _ensure_data_dir, _load_config, _save_config, _setup_notion
        _ensure_data_dir()
        config = _load_config()
        _setup_notion(config)
        _save_config(config)

    elif sub == "feishu":
        from brain.setup import _ensure_data_dir, _load_config, _save_config, _setup_feishu
        _ensure_data_dir()
        config = _load_config()
        _setup_feishu(config)
        _save_config(config)

    elif sub == "reinit-workspace":
        from brain.config import WORKSPACE_BASE
        from brain.session.manager import update_workspace_template

        if not WORKSPACE_BASE.exists():
            print("无 workspace 目录")
            return

        workspaces = [d for d in WORKSPACE_BASE.iterdir() if d.is_dir()]
        if not workspaces:
            print("无 workspace")
            return

        target = args[1] if len(args) > 1 else ""
        if target:
            workspaces = [ws for ws in workspaces if ws.name == target]
            if not workspaces:
                print(f"  未找到 workspace: {target}")
                return

        for ws in workspaces:
            update_workspace_template(ws, ws.name)
            print(f"  ✓ {ws.name} 模板已更新（用户内容已保留）")

    else:
        print("""\
用法: ccbrain config <subcommand>

子命令:
  show                          显示当前配置
  edit                          用编辑器打开 config.yaml
  path                          输出配置文件路径
  feishu                        重新配置飞书连接
  notion                        重新配置 Notion 连接
  lark-cli                      安装/配置飞书 CLI 工具
  reinit-workspace [name]       重新注入 workspace 模板（不指定 name 则全部更新）""")


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("ccbrain")
    except Exception:
        pass
    # fallback: 读 pyproject.toml（editable 模式）
    for candidate in [SRC_DIR / "pyproject.toml", SRC_DIR.parent / "pyproject.toml"]:
        if candidate.exists():
            import re
            m = re.search(r'version\s*=\s*"([^"]+)"', candidate.read_text())
            if m:
                return m.group(1)
    return "dev"


def main():  # pragma: no cover
    args = sys.argv[1:]
    cmd = args[0] if args else ""

    if cmd in ("--version", "-v"):
        print(f"ccbrain {_version()}")
        return

    match cmd:
        case "init":      cmd_init()
        case "config":    cmd_config(args[1:])
        case "run":       cmd_run()
        case "install":   cmd_install()
        case "uninstall": cmd_uninstall()
        case "start":     cmd_start()
        case "stop":      cmd_stop()
        case "restart":   cmd_restart()
        case "status":    cmd_status()
        case "logs":      cmd_logs(args[1] if len(args) > 1 else "brain")
        case _:           usage()


if __name__ == "__main__":
    main()
