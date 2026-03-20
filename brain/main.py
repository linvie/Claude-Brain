"""Brain Daemon 主模块 — 薄主循环，业务逻辑委托给 core/ 模块。"""

import time

from brain.config import (
    ACTIVE_INTERVAL,
    COOLDOWN_DURATION,
    COOLDOWN_INTERVAL,
    IDLE_INTERVAL,
    MAX_CONCURRENT,
    MAX_TASK_DURATION,
    WORKSPACE_BASE,
)
from brain.core.dispatcher import dispatch
from brain.core.outbox import check_all_outboxes
from brain.core.watchdog import watchdog
from brain.infra.db import get_db, running_task_count
from brain.infra.logger import log, log_scheduler
from brain.integrations.notion import fetch_ready_tasks


def main():
    log.info("Brain Daemon 启动")
    log.info(
        "配置: idle=%ds, active=%ds, cooldown=%ds/%ds, timeout=%ds, max_concurrent=%d",
        IDLE_INTERVAL, ACTIVE_INTERVAL, COOLDOWN_INTERVAL, COOLDOWN_DURATION,
        MAX_TASK_DURATION, MAX_CONCURRENT,
    )
    log.info("Workspace 根目录: %s", WORKSPACE_BASE)

    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    conn = get_db()

    cycle = 0
    last_task_done_time = 0  # 最近一次任务完成的时间戳

    try:
        while True:
            cycle += 1
            running_before = running_task_count(conn)

            # 1. 运行中任务：检查超时 + 收集结果
            if running_before > 0:
                watchdog(conn)
                check_all_outboxes(conn)

            # 检测是否有任务刚完成（running 数减少了）
            running_after = running_task_count(conn)
            if running_after < running_before:
                last_task_done_time = time.time()
                log.info("[loop] 检测到任务完成，进入 cooldown 轮询模式")

            # 2. 有空槽位：查询并分发新任务
            if running_after < MAX_CONCURRENT:
                ready_tasks = fetch_ready_tasks()
                if ready_tasks:
                    log_scheduler.info("发现 %d 个 Ready 任务, 当前运行 %d/%d", len(ready_tasks), running_after, MAX_CONCURRENT)
                for task in ready_tasks:
                    if running_task_count(conn) >= MAX_CONCURRENT:
                        log_scheduler.info("已达并发上限 %d，剩余任务下轮处理", MAX_CONCURRENT)
                        break
                    dispatch(conn, task)

            # 3. 选择轮询间隔
            running_now = running_task_count(conn)
            if running_now > 0:
                interval = ACTIVE_INTERVAL
            elif time.time() - last_task_done_time < COOLDOWN_DURATION:
                interval = COOLDOWN_INTERVAL
            else:
                interval = IDLE_INTERVAL

            log.debug("[loop] cycle=%d, running=%d, sleep=%ds", cycle, running_now, interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Brain Daemon 收到中断信号，退出")
    finally:
        conn.close()
        log.info("数据库连接已关闭")
