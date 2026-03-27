#!/bin/bash
# brain.sh — Claude Brain daemon 服务管理脚本
set -euo pipefail

LABEL="com.linvie.claude-brain"
BRAIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOGS_DIR="${BRAIN_DIR}/logs"
UV_PATH="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
DOMAIN="gui/$(id -u)"

generate_plist() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${UV_PATH}</string>
        <string>run</string>
        <string>python</string>
        <string>-m</string>
        <string>brain</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${BRAIN_DIR}</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOGS_DIR}/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOGS_DIR}/launchd.stderr.log</string>
</dict>
</plist>
EOF
}

is_loaded() {
    launchctl print "${DOMAIN}/${LABEL}" &>/dev/null
}

cmd_install() {
    if is_loaded; then
        echo "服务已安装，如需重新安装请先 ./brain.sh uninstall"
        exit 1
    fi
    mkdir -p "$LOGS_DIR"
    mkdir -p "$(dirname "$PLIST_PATH")"
    generate_plist > "$PLIST_PATH"
    launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
    echo "✓ 服务已安装并启动"
    echo "  plist: $PLIST_PATH"
    echo "  日志:  $LOGS_DIR/"
}

cmd_uninstall() {
    if ! is_loaded; then
        echo "服务未安装"
        # 清理残留 plist
        [ -f "$PLIST_PATH" ] && rm "$PLIST_PATH" && echo "已清理残留 plist"
        exit 0
    fi
    launchctl bootout "$DOMAIN/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✓ 服务已卸载"
}

cmd_start() {
    if ! is_loaded; then
        echo "服务未安装，请先运行 ./brain.sh install"
        exit 1
    fi
    launchctl kickstart "${DOMAIN}/${LABEL}"
    echo "✓ 服务已启动"
}

cmd_stop() {
    if ! is_loaded; then
        echo "服务未安装"
        exit 1
    fi
    launchctl kill SIGTERM "${DOMAIN}/${LABEL}"
    echo "✓ 已发送停止信号"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if ! is_loaded; then
        echo "服务未安装"
        exit 0
    fi

    local output
    output=$(launchctl print "${DOMAIN}/${LABEL}" 2>&1)

    local pid
    pid=$(echo "$output" | grep -oE 'pid = [0-9]+' | grep -oE '[0-9]+' || true)

    if [ -n "$pid" ]; then
        local elapsed
        elapsed=$(ps -o etime= -p "$pid" 2>/dev/null | xargs || true)
        echo "● running (PID ${pid}, uptime ${elapsed:-unknown})"
    else
        echo "● stopped"
    fi
}

cmd_logs() {
    local name="${1:-brain}"
    local log_file="${LOGS_DIR}/${name}.log"
    if [ ! -f "$log_file" ]; then
        echo "日志文件不存在: $log_file"
        echo "可用日志: brain, scheduler, cc, notion, launchd.stdout, launchd.stderr"
        exit 1
    fi
    tail -f "$log_file"
}

usage() {
    cat <<EOF
用法: ./brain.sh <command>

命令:
  install     安装 launchd 服务（注册并启动）
  uninstall   卸载服务
  start       启动服务
  stop        停止服务（优雅关闭）
  restart     重启服务
  status      查看运行状态
  logs [name] 查看日志（默认 brain，可选: scheduler, cc, notion）
EOF
}

case "${1:-}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    start)     cmd_start ;;
    stop)      cmd_stop ;;
    restart)   cmd_restart ;;
    status)    cmd_status ;;
    logs)      cmd_logs "${2:-}" ;;
    *)         usage ;;
esac
