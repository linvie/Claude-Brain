"""Brain Daemon 主模块 — 薄主循环，业务逻辑委托给 core/ 模块。"""

import time

from brain.config import ACTIVE_INTERVAL, IDLE_INTERVAL, MAX_TASK_DURATION, WORKSPACE_BASE
from brain.core.dispatcher import dispatch
from brain.core.outbox import check_all_outboxes
from brain.core.watchdog import watchdog
from brain.infra.db import get_db, has_running_tasks
from brain.infra.logger import log, log_scheduler
from brain.integrations.notion import fetch_ready_tasks


def main():
    log.info("Brain Daemon 启动")
    log.info("配置: idle=%ds, active=%ds, timeout=%ds", IDLE_INTERVAL, ACTIVE_INTERVAL, MAX_TASK_DURATION)
    log.info("Workspace 根目录: %s", WORKSPACE_BASE)

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
                ready_tasks = fetch_ready_tasks()
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
