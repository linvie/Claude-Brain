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


def _uv() -> str:
    return which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _is_loaded() -> bool:
    return _run(f"launchctl print {DOMAIN}/{LABEL}", check=False).returncode == 0


def _generate_plist() -> str:
    uv = _uv()
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
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

def cmd_init():
    from brain.setup import main as setup_main
    setup_main()


def cmd_run():
    if not DATA_DIR.exists():
        print(f"数据目录不存在: {DATA_DIR}")
        print("请先运行 ccbrain init")
        sys.exit(1)
    from brain.main import main
    asyncio.run(main())


def cmd_install():
    if not DATA_DIR.exists():
        print("请先运行 ccbrain init")
        sys.exit(1)
    if _is_loaded():
        print("服务已安装，如需重新安装请先 ccbrain uninstall")
        sys.exit(1)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_generate_plist())
    _run(f"launchctl bootstrap {DOMAIN} {PLIST_PATH}")
    print("✓ 服务已安装并启动")
    print(f"  plist: {PLIST_PATH}")
    print(f"  数据:  {DATA_DIR}/")


def cmd_uninstall():
    if not _is_loaded():
        print("服务未安装")
        if PLIST_PATH.exists():
            PLIST_PATH.unlink()
            print("已清理残留 plist")
        return
    _run(f"launchctl bootout {DOMAIN}/{LABEL}", check=False)
    PLIST_PATH.unlink(missing_ok=True)
    print("✓ 服务已卸载")


def cmd_start():
    if not _is_loaded():
        print("服务未安装，请先运行 ccbrain install")
        sys.exit(1)
    r = _run(f"launchctl kickstart {DOMAIN}/{LABEL}", check=False)
    if r.returncode != 0:
        print(f"启动失败（可能已在运行），尝试 ccbrain status 查看")
    else:
        print("✓ 服务已启动")


def cmd_stop():
    if not _is_loaded():
        print("服务未安装")
        sys.exit(1)
    r = _run(f"launchctl kill SIGTERM {DOMAIN}/{LABEL}", check=False)
    if r.returncode != 0:
        print("服务未在运行")
    else:
        print("✓ 已发送停止信号")


def cmd_restart():
    cmd_stop()
    time.sleep(1)
    cmd_start()


def cmd_status():
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


def cmd_logs(name: str = "brain"):
    log_file = LOG_DIR / f"{name}.log"
    if not log_file.exists():
        print(f"日志文件不存在: {log_file}")
        print("可用: brain, scheduler, cc, notion, feishu, session, memory, launchd.stdout, launchd.stderr")
        sys.exit(1)
    os.execvp("tail", ["tail", "-f", str(log_file)])


def usage():
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


def cmd_config(args: list[str]):
    """配置管理：查看、修改配置项，安装扩展工具。"""
    sub = args[0] if args else ""

    if sub == "edit":
        # 直接打开编辑器
        import os as _os
        editor = _os.environ.get("EDITOR", "vim")
        from brain.config import CONFIG_PATH
        if not CONFIG_PATH.exists():
            print(f"配置文件不存在，请先运行 ccbrain init")
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
        from brain.setup import _setup_notion, _load_config, _save_config, _ensure_data_dir
        _ensure_data_dir()
        config = _load_config()
        _setup_notion(config)
        _save_config(config)

    elif sub == "feishu":
        from brain.setup import _setup_feishu, _load_config, _save_config, _ensure_data_dir
        _ensure_data_dir()
        config = _load_config()
        _setup_feishu(config)
        _save_config(config)

    else:
        print("""\
用法: ccbrain config <subcommand>

子命令:
  show        显示当前配置
  edit        用编辑器打开 config.yaml
  path        输出配置文件路径
  feishu      重新配置飞书连接
  notion      重新配置 Notion 连接
  lark-cli    安装/配置飞书 CLI 工具""")


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("ccbrain")
    except Exception:
        pass
    # fallback: 读 pyproject.toml
    toml = SRC_DIR / "pyproject.toml"
    if toml.exists():
        import re
        m = re.search(r'version\s*=\s*"([^"]+)"', toml.read_text())
        if m:
            return m.group(1)
    return "dev"


def main():
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
