#!/usr/bin/env python3
"""Brain Daemon — 确定性调度器，负责轮询 Notion、管理 CC 进程、收集结果。"""

import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import yaml

from notion_client import NotionClient

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


CONFIG = load_config()

IDLE_INTERVAL = CONFIG["scheduler"]["idle_interval"]
ACTIVE_INTERVAL = CONFIG["scheduler"]["active_interval"]
MAX_TASK_DURATION = CONFIG["task"]["max_duration"]
WORKSPACE_BASE = Path(CONFIG["workspace"]["base_dir"]).expanduser()
DB_PATH = Path(CONFIG["database"]["path"])
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

# ---------------------------------------------------------------------------
# Notion 客户端
# ---------------------------------------------------------------------------

notion_cfg = CONFIG["notion"]
notion = NotionClient(
    token=notion_cfg["token"],
    task_db_id=notion_cfg["task_db_id"],
    project_db_id=notion_cfg["project_db_id"],
)

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

log_cfg = CONFIG["logging"]
log_dir = Path(log_cfg["dir"])
if not log_dir.is_absolute():
    log_dir = BASE_DIR / log_dir
log_dir.mkdir(parents=True, exist_ok=True)

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"

# 主日志 — 全量记录
log = logging.getLogger("brain")
log.setLevel(logging.DEBUG)

_main_handler = logging.FileHandler(log_dir / "brain.log")
_main_handler.setLevel(getattr(logging, log_cfg["level"]))
_main_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log.addHandler(_main_handler)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log.addHandler(_console_handler)

# 调度日志 — 只记录任务生命周期事件（dispatch / done / blocked / timeout）
log_scheduler = logging.getLogger("brain.scheduler")
_sched_handler = logging.FileHandler(log_dir / "scheduler.log")
_sched_handler.setLevel(logging.DEBUG)
_sched_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log_scheduler.addHandler(_sched_handler)

# CC 进程日志 — 记录 CC 启动、退出、stdout/stderr 摘要
log_cc = logging.getLogger("brain.cc")
_cc_handler = logging.FileHandler(log_dir / "cc.log")
_cc_handler.setLevel(logging.DEBUG)
_cc_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log_cc.addHandler(_cc_handler)

# Notion 交互日志 — 记录所有 Notion API 调用
log_notion = logging.getLogger("brain.notion")
_notion_handler = logging.FileHandler(log_dir / "notion.log")
_notion_handler.setLevel(logging.DEBUG)
_notion_handler.setFormatter(logging.Formatter(LOG_FORMAT))
log_notion.addHandler(_notion_handler)

# ---------------------------------------------------------------------------
# SQLite 状态管理
# ---------------------------------------------------------------------------


def init_db(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_runs (
            task_id        TEXT PRIMARY KEY,
            project_id     TEXT NOT NULL,
            status         TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            pid            INTEGER,
            start_time     INTEGER,
            end_time       INTEGER
        );

        CREATE TABLE IF NOT EXISTS workspaces (
            project_id     TEXT PRIMARY KEY,
            workspace_path TEXT NOT NULL,
            last_active    INTEGER
        );
        """
    )
    conn.commit()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Notion 交互（通过 REST API）
# ---------------------------------------------------------------------------


def fetch_ready_tasks_from_notion() -> list[dict]:
    """从 Notion Task 数据库获取所有 status=Ready 的任务，按 priority 排序。"""
    try:
        return notion.query_ready_tasks()
    except Exception as e:
        log_notion.error("查询 Ready 任务失败: %s", e)
        return []


def notion_update_status(task_id: str, status: str):
    """更新 Notion Task 的 status 字段。"""
    try:
        notion.update_task_status(task_id, status)
    except Exception as e:
        log_notion.error("更新状态失败: task=%s, status=%s, error=%s", task_id, status, e)


def notion_append_log(task_id: str, log_entry: str):
    """向 Notion Task 的 execution_log 字段追加一行日志。"""
    try:
        notion.append_execution_log(task_id, log_entry)
    except Exception as e:
        log_notion.error("追加日志失败: task=%s, error=%s", task_id, e)


# ---------------------------------------------------------------------------
# Workspace 管理
# ---------------------------------------------------------------------------


def prepare_workspace(project_id: str, repo_url: str | None) -> Path:
    """准备 workspace：存在则 git pull，不存在则 git clone 或创建空目录。"""
    ws = WORKSPACE_BASE / project_id
    if ws.exists():
        log.info("[workspace] 已存在，执行 git pull: %s", ws)
        result = subprocess.run(["git", "pull"], cwd=ws, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("[workspace] git pull 失败: %s", result.stderr.strip())
        else:
            log.debug("[workspace] git pull 输出: %s", result.stdout.strip())
    elif repo_url:
        log.info("[workspace] 克隆仓库 %s → %s", repo_url, ws)
        result = subprocess.run(["git", "clone", repo_url, str(ws)], capture_output=True, text=True)
        if result.returncode != 0:
            log.error("[workspace] git clone 失败: %s", result.stderr.strip())
        else:
            log.info("[workspace] 克隆完成")
    else:
        log.info("[workspace] 创建新项目: %s", ws)
        ws.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=ws, capture_output=True)

    return ws


# ---------------------------------------------------------------------------
# inbox / outbox 协议
# ---------------------------------------------------------------------------


def write_inbox(workspace: Path, task: dict):
    """向 workspace/inbox.json 写入任务描述。"""
    inbox_data = {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "project_id": task["project_id"],
        "description": task["description"],
    }
    if task.get("context"):
        inbox_data["context"] = task["context"]

    inbox_path = workspace / "inbox.json"
    inbox_path.write_text(json.dumps(inbox_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已写入 inbox.json: task_id=%s", task["task_id"])


VALID_STATUSES = {"TASK_DONE", "TASK_BLOCKED", "TASK_PROGRESS"}


def validate_outbox(content: str) -> tuple[bool, str]:
    """校验 outbox.json 格式，返回 (is_valid, error_message)。"""
    if not content.strip():
        return False, "outbox.json 为空"

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return False, f"JSON 解析失败: {e}"

    if not isinstance(data, dict):
        return False, "根元素必须是 JSON 对象"

    status = data.get("status")
    if not status or status not in VALID_STATUSES:
        return False, f"status 无效: {status!r}，合法值: {VALID_STATUSES}"

    if not data.get("summary"):
        return False, "缺少 summary 字段或内容为空"

    if status == "TASK_BLOCKED" and not data.get("reason"):
        return False, "TASK_BLOCKED 必须提供 reason 字段"

    if status == "TASK_PROGRESS" and not data.get("stage"):
        return False, "TASK_PROGRESS 必须提供 stage 字段"

    return True, ""


def parse_outbox(content: str) -> dict:
    """解析 outbox.json，返回 dict。调用前应先 validate。"""
    return json.loads(content)


# ---------------------------------------------------------------------------
# CC 进程管理
# ---------------------------------------------------------------------------


def install_workspace_template(workspace: Path, task_type: str, task: dict | None = None):
    """将角色模板目录和共享文件复制到 workspace。

    复制顺序：shared/ → {task_type}/ → 覆盖写入。
    已存在的项目文件（非模板文件）不会被覆盖。

    对 planner 类型，额外注入 brain_config.json 供 CC 读取数据库 ID。
    """
    shared_dir = BASE_DIR / "templates" / "shared"
    role_dir = BASE_DIR / "templates" / task_type

    if not role_dir.exists():
        log_cc.error("角色模板目录不存在: %s", role_dir)
        return

    # 复制共享模板（outbox.json、OUTBOX_FORMAT.md）
    if shared_dir.exists():
        for src in shared_dir.iterdir():
            dest = workspace / src.name
            if src.is_file():
                shutil.copy2(src, dest)
                log_cc.debug("复制共享文件: %s → %s", src.name, dest)

    # 复制角色模板（CLAUDE.md、.claude/settings.json、WORKFLOW.md 等）
    for src in role_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(role_dir)
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log_cc.debug("复制角色文件: %s → %s", rel, dest)

    # Planner 需要知道 Notion 数据库 ID 和 project_id 以创建 Task
    if task_type == "planner" and task:
        brain_config = {
            "task_db_id": notion_cfg["task_db_id"],
            "project_db_id": notion_cfg["project_db_id"],
            "project_id": task.get("project_id", ""),
        }
        config_path = workspace / "brain_config.json"
        config_path.write_text(json.dumps(brain_config, indent=2), encoding="utf-8")
        log_cc.debug("注入 brain_config.json: %s", brain_config)

    log_cc.info("模板安装完成: type=%s, workspace=%s", task_type, workspace)


def launch_cc(workspace: Path, task_type: str, task: dict | None = None) -> int:
    """启动 CC 进程，返回 PID。

    根据 config.yaml 中 roles 配置组装 --allowedTools / --disallowedTools 参数。
    权限完全由 --allowedTools / --disallowedTools + .claude/settings.json 控制。
    """
    install_workspace_template(workspace, task_type, task)

    # 读取 inbox.json 作为 prompt
    inbox_path = workspace / "inbox.json"
    prompt = inbox_path.read_text(encoding="utf-8") if inbox_path.exists() else ""

    cmd = ["claude", "--print", prompt]

    # 从 config 读取角色权限（如果配置了 roles）
    roles_cfg = CONFIG.get("roles", {}).get(task_type, {})
    allowed = roles_cfg.get("allowed_tools", [])
    disallowed = roles_cfg.get("disallowed_tools", [])

    if allowed:
        cmd.extend(["--allowedTools", ",".join(allowed)])
    if disallowed:
        cmd.extend(["--disallowedTools", ",".join(disallowed)])

    log_cc.info("启动 %s CC: workspace=%s", task_type, workspace)
    log_cc.debug("CC 命令: %s", " ".join(cmd[:3]) + " ...")
    if allowed:
        log_cc.debug("allowed_tools: %s", allowed)
    if disallowed:
        log_cc.debug("disallowed_tools: %s", disallowed)

    proc = subprocess.Popen(
        cmd,
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    log_cc.info("%s CC 已启动: PID=%d, workspace=%s", task_type, proc.pid, workspace)
    return proc.pid


# ---------------------------------------------------------------------------
# 核心调度逻辑
# ---------------------------------------------------------------------------


def has_running_tasks(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_runs WHERE status = 'running'"
    ).fetchone()
    return row["cnt"] > 0


def project_has_running_task(conn: sqlite3.Connection, project_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_runs WHERE project_id = ? AND status = 'running'",
        (project_id,),
    ).fetchone()
    return row["cnt"] > 0


def all_done(conn: sqlite3.Connection, task_ids: list[str]) -> bool:
    if not task_ids:
        return True
    placeholders = ",".join("?" for _ in task_ids)
    row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM task_runs WHERE task_id IN ({placeholders}) AND status = 'done'",
        task_ids,
    ).fetchone()
    return row["cnt"] == len(task_ids)


def dispatch(conn: sqlite3.Connection, task: dict):
    """分发一个 Ready 任务。"""
    task_id = task["task_id"]
    project_id = task["project_id"]
    task_type = task.get("task_type", "executor")

    log_scheduler.info("开始分发: task=%s, project=%s, type=%s", task_id, project_id, task_type)

    # 1. 检查依赖
    blocked_by = task.get("blocked_by", [])
    if blocked_by and not all_done(conn, blocked_by):
        log_scheduler.info("跳过: task=%s, 原因=依赖未完成 blocked_by=%s", task_id, blocked_by)
        return

    # 2. 同 project 串行锁
    if project_has_running_task(conn, project_id):
        log_scheduler.info("跳过: task=%s, 原因=项目 %s 已有运行中任务", task_id, project_id)
        return

    # 3. 准备 workspace
    workspace = prepare_workspace(project_id, task.get("repo_url"))

    # 4. 写入 inbox.md
    write_inbox(workspace, task)

    # 5. 更新 Notion 状态
    notion_update_status(task_id, "Running")

    # 6. 启动 CC
    pid = launch_cc(workspace, task_type, task)

    # 7. 记录到 SQLite
    start_time = int(time.time())
    conn.execute(
        """INSERT OR REPLACE INTO task_runs
           (task_id, project_id, status, workspace_path, pid, start_time)
           VALUES (?, ?, 'running', ?, ?, ?)""",
        (task_id, project_id, str(workspace), pid, start_time),
    )
    conn.commit()
    log_scheduler.info(
        "分发完成: task=%s, PID=%d, workspace=%s, start_time=%s",
        task_id, pid, workspace, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time)),
    )


def watchdog(conn: sqlite3.Connection):
    """检测超时任务并终止。"""
    now = int(time.time())
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE status = 'running'"
    ).fetchall()

    for task in rows:
        elapsed = now - task["start_time"]
        task_id = task["task_id"]
        pid = task["pid"]

        # 检查进程是否还存活
        try:
            os.kill(pid, 0)  # signal 0 不发信号，仅检查进程是否存在
        except ProcessLookupError:
            log_scheduler.warning("进程已消失: task=%s, PID=%d, elapsed=%dm，标记为异常退出", task_id, pid, elapsed // 60)
            log_cc.warning("CC 进程异常退出: PID=%d, task=%s", pid, task_id)
            conn.execute(
                "UPDATE task_runs SET status = 'format_error', end_time = ? WHERE task_id = ?",
                (now, task_id),
            )
            conn.commit()
            notion_update_status(task_id, "Blocked")
            notion_append_log(task_id, f"[{time.strftime('%Y-%m-%d %H:%M')}] CC 进程异常退出，需人工检查")
            continue

        if elapsed > MAX_TASK_DURATION:
            log_scheduler.warning("超时: task=%s, PID=%d, elapsed=%dm (max=%dm)", task_id, pid, elapsed // 60, MAX_TASK_DURATION // 60)
            log_cc.warning("终止超时 CC 进程: PID=%d, task=%s", pid, task_id)

            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            conn.execute(
                "UPDATE task_runs SET status = 'timeout', end_time = ? WHERE task_id = ?",
                (now, task_id),
            )
            conn.commit()

            notion_update_status(task_id, "Timeout")
            notion_append_log(
                task_id, f"[{time.strftime('%Y-%m-%d %H:%M')}] 任务超时（{elapsed // 60}分钟），已终止"
            )
        else:
            log.debug("[watchdog] task=%s, PID=%d, 已运行 %dm/%dm", task_id, pid, elapsed // 60, MAX_TASK_DURATION // 60)


def check_all_outboxes(conn: sqlite3.Connection):
    """轮询所有运行中任务的 outbox.json。"""
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE status = 'running'"
    ).fetchall()

    log.debug("[outbox] 检查 %d 个运行中任务的 outbox", len(rows))

    for task in rows:
        outbox_path = Path(task["workspace_path"]) / "outbox.json"
        if not outbox_path.exists():
            log.debug("[outbox] task=%s, outbox.json 不存在，跳过", task["task_id"])
            continue

        content = outbox_path.read_text(encoding="utf-8")
        if not content.strip() or content.strip() == "{}":
            continue

        log_scheduler.info("收到 outbox: task=%s, 内容长度=%d", task["task_id"], len(content))
        log.debug("[outbox] task=%s, 原始内容:\n%s", task["task_id"], content)

        handle_outbox(conn, task["task_id"], content)

        # 处理完后重置为空 JSON（避免重复处理）
        outbox_path.write_text("{}", encoding="utf-8")


def handle_outbox(conn: sqlite3.Connection, task_id: str, content: str):
    """处理 outbox.json 内容。"""
    now_str = time.strftime("%Y-%m-%d %H:%M")

    is_valid, error_msg = validate_outbox(content)
    if not is_valid:
        log_scheduler.error("outbox 格式异常: task=%s, error=%s", task_id, error_msg)
        log.error("[outbox] 校验失败: task=%s, error=%s, 内容:\n%s", task_id, error_msg, content)
        conn.execute(
            "UPDATE task_runs SET status = 'format_error', end_time = ? WHERE task_id = ?",
            (int(time.time()), task_id),
        )
        conn.commit()
        notion_update_status(task_id, "Blocked")
        notion_append_log(task_id, f"[{now_str}] outbox 格式异常: {error_msg}")
        return

    data = parse_outbox(content)
    status = data["status"]
    summary = data["summary"]
    log_entry = f"[{now_str}] {summary}"

    if status == "TASK_DONE":
        notion_append_log(task_id, log_entry)
        notion_update_status(task_id, "Done")
        end_time = int(time.time())
        conn.execute(
            "UPDATE task_runs SET status = 'done', end_time = ? WHERE task_id = ?",
            (end_time, task_id),
        )
        conn.commit()

        # 计算运行时长
        row = conn.execute(
            "SELECT start_time, workspace_path FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        duration = (end_time - row["start_time"]) // 60 if row else 0
        log_scheduler.info("完成: task=%s, 耗时=%dm, summary=%s", task_id, duration, summary[:100])

        # 更新 workspace last_active
        if row:
            conn.execute(
                """INSERT OR REPLACE INTO workspaces (project_id, workspace_path, last_active)
                   VALUES ((SELECT project_id FROM task_runs WHERE task_id = ?), ?, ?)""",
                (task_id, row["workspace_path"], end_time),
            )
            conn.commit()

    elif status == "TASK_BLOCKED":
        reason = data["reason"]
        notion_append_log(task_id, f"[{now_str}] 阻塞：{reason}")
        notion_update_status(task_id, "Blocked")
        conn.execute(
            "UPDATE task_runs SET status = 'blocked', end_time = ? WHERE task_id = ?",
            (int(time.time()), task_id),
        )
        conn.commit()
        log_scheduler.warning("阻塞: task=%s, reason=%s", task_id, reason)

    elif status == "TASK_PROGRESS":
        stage = data["stage"]
        notion_append_log(task_id, log_entry)
        log_scheduler.info("进度: task=%s, stage=%s, summary=%s", task_id, stage, summary[:100])


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def main():
    log.info("Brain Daemon 启动")
    log.info("配置: idle=%ds, active=%ds, timeout=%ds", IDLE_INTERVAL, ACTIVE_INTERVAL, MAX_TASK_DURATION)
    log.info("Workspace 根目录: %s", WORKSPACE_BASE)
    log.info("数据库: %s", DB_PATH)

    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    conn = get_db()

    cycle = 0
    try:
        while True:
            cycle += 1
            watchdog(conn)

            if has_running_tasks(conn):
                check_all_outboxes(conn)
                log.debug("[loop] cycle=%d, mode=active, sleep=%ds", cycle, ACTIVE_INTERVAL)
                time.sleep(ACTIVE_INTERVAL)
            else:
                ready_tasks = fetch_ready_tasks_from_notion()
                if ready_tasks:
                    log_scheduler.info("发现 %d 个 Ready 任务", len(ready_tasks))
                for task in ready_tasks:
                    dispatch(conn, task)
                log.debug("[loop] cycle=%d, mode=idle, ready=%d, sleep=%ds", cycle, len(ready_tasks), IDLE_INTERVAL)
                time.sleep(IDLE_INTERVAL)
    except KeyboardInterrupt:
        log.info("Brain Daemon 收到中断信号，退出")
    finally:
        conn.close()
        log.info("数据库连接已关闭")


if __name__ == "__main__":
    main()
