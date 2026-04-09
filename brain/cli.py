"""Brain CLI — 全局命令行入口，任何目录下均可运行。

用法：
    brain init          交互式配置向导
    brain run           前台运行（调试用）
    brain install       注册 launchd 服务并启动
    brain uninstall     卸载 launchd 服务
    brain start         启动服务
    brain stop          停止服务
    brain restart       重启服务
    brain status        查看运行状态
    brain logs [name]   tail -f 日志
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

# 项目根目录：brain/cli.py → brain/ → 项目根
BRAIN_DIR = Path(__file__).resolve().parent.parent

LABEL = "com.linvie.claude-brain"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOGS_DIR = BRAIN_DIR / "logs"
DOMAIN = f"gui/{os.getuid()}"


def _uv() -> str:
    """找到 uv 可执行文件路径。"""
    from shutil import which
    return which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _is_loaded() -> bool:
    r = _run(f"launchctl print {DOMAIN}/{LABEL}", check=False)
    return r.returncode == 0


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
        <string>python</string>
        <string>-m</string>
        <string>brain</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BRAIN_DIR}</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOGS_DIR}/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{LOGS_DIR}/launchd.stderr.log</string>
</dict>
</plist>"""


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_init():
    from brain.setup import main as setup_main
    setup_main()


def cmd_run():
    from brain.main import main
    asyncio.run(main())


def cmd_install():
    if _is_loaded():
        print("服务已安装，如需重新安装请先 brain uninstall")
        sys.exit(1)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_generate_plist())
    _run(f"launchctl bootstrap {DOMAIN} {PLIST_PATH}")
    print("✓ 服务已安装并启动")
    print(f"  plist: {PLIST_PATH}")
    print(f"  日志:  {LOGS_DIR}/")


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
        print("服务未安装，请先运行 brain install")
        sys.exit(1)
    _run(f"launchctl kickstart {DOMAIN}/{LABEL}")
    print("✓ 服务已启动")


def cmd_stop():
    if not _is_loaded():
        print("服务未安装")
        sys.exit(1)
    _run(f"launchctl kill SIGTERM {DOMAIN}/{LABEL}")
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
    import re
    pid_match = re.search(r"pid = (\d+)", r.stdout)
    if pid_match:
        pid = pid_match.group(1)
        elapsed_r = _run(f"ps -o etime= -p {pid}", check=False)
        elapsed = elapsed_r.stdout.strip() or "unknown"
        print(f"● running (PID {pid}, uptime {elapsed})")
    else:
        print("● stopped")


def cmd_logs(name: str = "brain"):
    log_file = LOGS_DIR / f"{name}.log"
    if not log_file.exists():
        print(f"日志文件不存在: {log_file}")
        print("可用: brain, scheduler, cc, notion, feishu, session, memory, launchd.stdout, launchd.stderr")
        sys.exit(1)
    os.execvp("tail", ["tail", "-f", str(log_file)])


def usage():
    print("""\
用法: brain <command>

命令:
  init        交互式配置向导（首次使用）
  run         前台运行（调试用，Ctrl+C 停止）
  install     注册 launchd 服务并启动
  uninstall   卸载服务
  start       启动服务
  stop        停止服务（优雅关闭）
  restart     重启服务
  status      查看运行状态
  logs [name] 查看日志（brain, scheduler, cc, notion, feishu, session, memory）""")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""

    match cmd:
        case "init":      cmd_init()
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
