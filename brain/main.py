"""Brain Daemon 主模块 — 薄主循环，业务逻辑委托给 core/ 模块。"""

import time

from brain.config import ACTIVE_INTERVAL, IDLE_INTERVAL, MAX_CONCURRENT, MAX_TASK_DURATION, WORKSPACE_BASE
from brain.core.dispatcher import dispatch
from brain.core.outbox import check_all_outboxes
from brain.core.watchdog import watchdog
from brain.infra.db import get_db, running_task_count
from brain.infra.logger import log, log_scheduler
from brain.integrations.notion import fetch_ready_tasks


def main():
    log.info("Brain Daemon 启动")
    log.info(
        "配置: idle=%ds, active=%ds, timeout=%ds, max_concurrent=%d",
        IDLE_INTERVAL, ACTIVE_INTERVAL, MAX_TASK_DURATION, MAX_CONCURRENT,
    )
    log.info("Workspace 根目录: %s", WORKSPACE_BASE)

    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    conn = get_db()

    cycle = 0
    try:
        while True:
            cycle += 1
            running = running_task_count(conn)

            # 1. 运行中任务：检查超时 + 收集结果
            if running > 0:
                watchdog(conn)
                check_all_outboxes(conn)

            # 2. 有空槽位：查询并分发新任务
            if running < MAX_CONCURRENT:
                ready_tasks = fetch_ready_tasks()
                if ready_tasks:
                    log_scheduler.info("发现 %d 个 Ready 任务, 当前运行 %d/%d", len(ready_tasks), running, MAX_CONCURRENT)
                for task in ready_tasks:
                    if running_task_count(conn) >= MAX_CONCURRENT:
                        log_scheduler.info("已达并发上限 %d，剩余任务下轮处理", MAX_CONCURRENT)
                        break
                    dispatch(conn, task)

            # 3. 根据是否有运行中任务选择轮询间隔
            interval = ACTIVE_INTERVAL if running_task_count(conn) > 0 else IDLE_INTERVAL
            log.debug("[loop] cycle=%d, running=%d, sleep=%ds", cycle, running_task_count(conn), interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Brain Daemon 收到中断信号，退出")
    finally:
        conn.close()
        log.info("数据库连接已关闭")
