# Claude Brain — 技术设计文档 v1.0

> 版本：1.0 | 日期：2026-03-19 | 状态：待审阅

---

## 一、项目概述

### 1.1 目标

构建一个以 Notion 为输入层、Claude Code 为执行层、Notion 为状态追踪层的个人异步任务自动化系统。

核心使用场景：晚上在 Notion 写项目想法或任务 → 系统夜间自动执行 → 早上醒来查看 Notion 中的执行状态和日志。

### 1.2 设计原则

1. **Brain 是确定性调度器**：Brain daemon 只做轮询、进程管理、结果收集、通知，不包含任何业务推理逻辑。
2. **CC 是执行者**：Claude Code 负责任务理解、技术拆解和代码实现，通过 CLAUDE.md 注入角色定义。
3. **职责边界清晰**：Notion 管"要什么"，CC 管"怎么做"，Brain 管"什么时候做、做完了告诉谁"。
4. **渐进式实现**：设计支持 Phase 1 MVP 快速跑通，后续功能可在不破坏现有结构的前提下叠加。

---

## 二、系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    Notion（输入层）                   │
│           Project 数据库 + Task 数据库                │
└─────────────────────┬───────────────────────────────┘
                      │ Notion MCP 读取（低频轮询）
                      ▼
┌─────────────────────────────────────────────────────┐
│              Brain Daemon（Python 常驻进程）          │
│              Mac Mini，launchd 保活                  │
│                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  Scheduler  │  │ Process Mgr  │  │  SQLite DB │ │
│  │  定时轮询   │  │  进程管理    │  │  状态管理  │ │
│  └─────────────┘  └──────────────┘  └────────────┘ │
└──────────┬──────────────────┬───────────────────────┘
           │ subprocess 启动  │ 轮询 outbox.md
           ▼                  ▼
┌──────────────────────────────────────────────────────┐
│         Claude Code 进程 A / B / N                   │
│         每个进程绑定独立 workspace                    │
│                                                      │
│   inbox.md（Brain 写）→ CC 读取并执行                │
│   outbox.md（CC 写）→ Brain 轮询读取                 │
└──────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────┐
│              结果处理层                               │
│   Brain → Notion MCP 写回状态 + 日志                 │
│   Brain → Telegram 通知                              │
└──────────────────────────────────────────────────────┘
```

### 2.2 参考实现

本系统架构参考 Symphony（OpenAI，2026-03-05 开源）的核心设计：

| Symphony 组件 | 本系统对应 |
|---|---|
| Linear issue tracker | Notion 数据库 |
| Elixir Orchestrator | Brain daemon（Python） |
| Codex AppServer | Claude Code |
| per-issue workspace（git clone） | per-project workspace（持久化） |
| WORKFLOW.md | CLAUDE.md（per-project）+ WORKFLOW.md（全局） |

**与 Symphony 的关键差异**：Symphony 使用 per-task workspace，本系统使用 per-project persistent workspace，原因见第四章。

---

## 三、CC 角色设计

系统中存在两种 Claude Code 角色，通过 Notion Task 的 `task_type` 字段区分，Brain 根据类型决定启动方式。

### 3.1 Planner CC（设计阶段）

**职责**：将模糊的项目需求分解为结构化的可执行 Task 列表，写入 Notion。

**权限**：拥有 Notion MCP 写入权限。

**工作流程**：
1. 读取 inbox.md 中的需求描述 + WORKFLOW.md 全局上下文
2. 输出任务拆解方案到 outbox.md（不直接写 Notion）
3. Brain 将方案推送 Telegram，等待用户确认
4. 用户确认后，Brain 向 inbox.md 写入 `CONFIRMED` 信号
5. Planner CC 读到确认后，正式将 Task 列表写入 Notion

**任务拆解规范（写入 CLAUDE.md）**：
- 每个 Task = 一次 CC 不被打断能完成的工作量
- 先写用户可感知的 milestone，再细化每个 milestone 内的 task
- 有依赖关系的任务必须标注 `blocked_by`
- 只写"完成后能做什么"，不写技术实现细节

### 3.2 Executor CC（开发阶段）

**职责**：执行具体的开发任务，在 workspace 中完成代码实现。

**权限**：无 Notion MCP 写权限（只读或不挂载）。

**工作流程**：
1. 读取 inbox.md 中的任务描述
2. 使用 TodoWrite 自行拆解执行步骤
3. 执行期间定期写入 `TASK_PROGRESS` 到 outbox.md
4. 完成后写入 `TASK_DONE` 到 outbox.md

---

## 四、Workspace 设计

### 4.1 设计决策：per-project persistent workspace

**不采用 per-task workspace 的原因**：

| 问题 | per-task 的问题 |
|---|---|
| CC session 连续性 | 每次新目录 = 新 session，无法 `--continue`，丢失上下文 |
| clone 开销 | 每个任务都要完整 clone，大型仓库效率低 |
| 同项目多任务并发 | 任务 A 改动未 push 时，任务 B clone 了旧版本，状态不一致 |

**采用 per-project persistent workspace**：
- workspace 以 `project_id` 命名，跨任务复用
- Brain 启动 CC 前检查本地是否存在该 project 的 workspace
  - 存在 → `git pull` 拉最新，启动 CC（`--continue` 接续上次 session）
  - 不存在 → `git clone` 建新 workspace，启动 CC

### 4.2 TTL 清理机制

- 每次 CC 完成任务，更新 workspace 的 `.last_active` 时间戳
- 独立 cleanup job 每天运行一次，删除超过 N 天无活动的 workspace
- 下次遇到同 project 的任务时重新 clone，CC 开启新 session

### 4.3 两种项目类型

**类型 A：全新项目**
```
Brain 在 ~/brain-workspaces/{project_id}/ 创建新目录
CC 从零开始构建
完成后：目录即为项目代码，可 git push 到远程
```

**类型 B：已有项目**
```
Notion Task 必须包含 repo_url 字段
Brain 启动前 git clone 该 repo 到 workspace
CC 在本地 workspace 工作
完成后：CC 提 PR，不直接修改原始 repo
```

### 4.4 并发限制

同一个 project 的多个 Ready 任务，Brain 保证串行执行（per-project 锁），不并发启动多个 CC 进程操作同一 workspace。同一个 project 的多个任务在同一次 CC session 中顺序处理。

---

## 五、Notion 数据库设计

### 5.1 Project 数据库

| 字段名 | 类型 | 说明 |
|---|---|---|
| project_name | Title | 项目名称 |
| project_type | Select | `new` / `existing` |
| repo_url | URL | GitHub 仓库地址（existing 类型必填） |
| status | Select | `Active` / `Paused` / `Archived` |
| description | Text | 项目背景描述 |

### 5.2 Task 数据库

| 字段名 | 类型 | 说明 |
|---|---|---|
| task_name | Title | 任务名称（一句话） |
| description | Text | 2-5句，说清楚要做什么、有什么约束 |
| task_type | Select | `planner` / `executor` |
| project | Relation | 关联到 Project 数据库 |
| blocked_by | Relation（self） | 依赖的前置任务，Brain 检测到未完成则跳过 |
| status | Select | `Pending` / `Ready` / `Running` / `Done` / `Blocked` / `Timeout` |
| priority | Select | `High` / `Normal` / `Low`，Brain 按此排序 |
| execution_log | Text | Brain append 执行日志（时间戳 + 进度摘要） |

### 5.3 Task Status 流转

```
Pending  →（手动标记）→  Ready
Ready    →（Brain 读到）→  Running
Running  →（TASK_DONE）→  Done
Running  →（TASK_BLOCKED）→  Blocked
Running  →（超时）→  Timeout
Blocked  →（手动解除）→  Ready
```

---

## 六、目录结构

```
~/claude-brain/
├── brain.py                  # Brain daemon 主程序
├── state.db                  # SQLite 状态数据库
├── WORKFLOW.md               # 全局工作流描述，注入 Planner CC 上下文
├── config.yaml               # Brain 配置（轮询间隔、超时时间等）
├── logs/
│   └── brain.log             # Brain 运行日志
└── templates/
    ├── CLAUDE_planner.md     # Planner CC 的 CLAUDE.md 模板
    └── CLAUDE_executor.md    # Executor CC 的 CLAUDE.md 模板

~/brain-workspaces/
└── {project_id}/             # 以 Notion Project page ID 命名
    ├── .git/
    ├── CLAUDE.md             # 从模板生成，包含项目特定信息
    ├── inbox.md              # Brain 写入，CC 读取
    ├── outbox.md             # CC 写入，Brain 轮询读取
    ├── .last_active          # 最后活跃时间戳（Unix timestamp）
    └── [项目代码文件...]
```

---

## 七、inbox / outbox 协议

### 7.1 inbox.md 格式（Brain 写）

```markdown
# Task
task_id: notion_page_id_xxx
task_type: executor
project_id: notion_project_id_yyy

# Description
[用户在 Notion 中填写的任务描述，原文复制]

# Context
[可选：上一个任务的产出摘要，或依赖任务的结果]
```

### 7.2 outbox.md 格式（CC 写）

```markdown
# Status
[TASK_DONE / TASK_BLOCKED:原因 / TASK_PROGRESS:阶段描述]

# Summary
[一段话描述本次完成或进展的内容]

# Artifacts
[可选：产出物路径、PR 链接等]
```

**Status Token 定义**：

| Token | 含义 | Brain 行为 |
|---|---|---|
| `TASK_DONE` | 任务完成 | 更新 Notion 状态为 Done，append 日志，发 Telegram |
| `TASK_BLOCKED:原因` | 遇到阻塞，需人工介入 | 更新 Notion 状态为 Blocked，发 Telegram 告警 |
| `TASK_PROGRESS:描述` | 长任务中途进度汇报 | Append 进度日志到 Notion，继续等待 |

### 7.3 格式校验

Brain 读取 outbox.md 后，先执行 validator，不通过则发 Telegram 告警，不写 Notion：

```python
def validate_outbox(content: str) -> bool:
    if not content.strip():
        return False
    lines = content.strip().split('\n')
    valid_tokens = ['TASK_DONE', 'TASK_BLOCKED:', 'TASK_PROGRESS:']
    # 规则1：# Status section 的下一行必须是合法 token
    if not any(lines[0].startswith(t) for t in valid_tokens):
        return False
    # 规则2：必须包含 # Status 和 # Summary
    if '# Status' not in content or '# Summary' not in content:
        return False
    return True
```

**CLAUDE.md 中的强制约束**：CC 完成每个阶段后必须严格按照 outbox.md 格式写入，Status token 必须是 `# Status` section 的第一行，不得有前置说明或额外内容。

---

## 八、Brain Daemon 设计

### 8.1 SQLite 状态表

```sql
-- 任务运行状态
CREATE TABLE IF NOT EXISTS task_runs (
    task_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    status        TEXT NOT NULL,  -- running / done / blocked / timeout / format_error
    workspace_path TEXT NOT NULL,
    pid           INTEGER,
    start_time    INTEGER,        -- Unix timestamp
    end_time      INTEGER
);

-- Workspace 元数据
CREATE TABLE IF NOT EXISTS workspaces (
    project_id    TEXT PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    last_active   INTEGER         -- Unix timestamp
);
```

### 8.2 主循环逻辑

```python
IDLE_INTERVAL   = 900   # 无任务时：每 15 分钟扫描 Notion
ACTIVE_INTERVAL = 30    # 有任务时：每 30 秒扫描 outbox
MAX_TASK_DURATION = 7200  # 任务超时上限：2 小时

while True:
    watchdog()  # 每轮都检查超时任务

    if has_running_tasks():
        check_all_outboxes()
        sleep(ACTIVE_INTERVAL)
    else:
        ready_tasks = fetch_ready_tasks_from_notion()
        for task in ready_tasks:
            dispatch(task)
        sleep(IDLE_INTERVAL)
```

### 8.3 任务分发逻辑

```python
def dispatch(task):
    # 1. 检查依赖是否完成
    if task.blocked_by and not all_done(task.blocked_by):
        return  # 跳过，等依赖完成

    # 2. 检查同 project 是否有任务在运行（串行锁）
    if project_has_running_task(task.project_id):
        return  # 跳过，同 project 串行执行

    # 3. 准备 workspace
    workspace = prepare_workspace(task.project_id, task.repo_url)

    # 4. 写入 inbox.md
    write_inbox(workspace, task)

    # 5. 更新 Notion 状态为 Running
    notion.update_status(task.task_id, 'Running')

    # 6. 启动对应类型的 CC
    if task.task_type == 'planner':
        pid = launch_planner_cc(workspace)
    else:
        pid = launch_executor_cc(workspace)

    # 7. 记录到 SQLite
    db.insert_task_run(task.task_id, task.project_id, workspace, pid)
```

### 8.4 Watchdog（超时检测）

```python
def watchdog():
    running_tasks = db.get_running_tasks()
    for task in running_tasks:
        elapsed = now() - task.start_time
        if elapsed > MAX_TASK_DURATION:
            kill_process(task.pid)
            db.update_status(task.task_id, 'timeout')
            notion.update_status(task.task_id, 'Timeout')
            telegram.notify(f"任务 {task.task_id} 已超时（{elapsed//60}分钟），已终止")
```

### 8.5 结果处理逻辑

```python
def handle_outbox(task_id, content):
    if not validate_outbox(content):
        db.update_status(task_id, 'format_error')
        notion.update_status(task_id, 'Blocked')
        notion.append_log(task_id, f"[{now_str()}] ❌ outbox 格式异常，需人工检查")
        return

    status_line = parse_status(content)
    summary = parse_summary(content)
    timestamp = now_str()
    log_entry = f"[{timestamp}] {summary}"

    if status_line == 'TASK_DONE':
        notion.append_log(task_id, log_entry)
        notion.update_status(task_id, 'Done')
        db.update_status(task_id, 'done')

    elif status_line.startswith('TASK_BLOCKED:'):
        reason = status_line.split(':', 1)[1]
        notion.append_log(task_id, f"[{timestamp}] ⚠️ 阻塞：{reason}")
        notion.update_status(task_id, 'Blocked')
        db.update_status(task_id, 'blocked')

    elif status_line.startswith('TASK_PROGRESS:'):
        notion.append_log(task_id, log_entry)
        # 不结束任务，继续等待
```

---

## 九、CLAUDE.md 模板

### 9.1 Executor CLAUDE.md

```markdown
# 角色定义
你是一个在隔离 workspace 中独立工作的工程师 Agent。
你不与用户直接交流，通过文件与调度系统通信。

# 工作流程
1. 读取 inbox.md，理解任务目标
2. 使用 TodoWrite 将任务拆解为子步骤
3. 执行代码实现
4. 每完成一个主要阶段，向 outbox.md 写入 TASK_PROGRESS
5. 全部完成后，向 outbox.md 写入 TASK_DONE

# outbox.md 写入规范（强制）
每次写入必须严格遵循以下格式，不得有任何前置说明：

# Status
TASK_DONE

# Summary
[一段话描述做了什么]

# Artifacts
[可选：产出物路径或链接]

# 约束
- 遇到无法继续的问题，写入 TASK_BLOCKED:具体原因，不要尝试绕过
- 不操作 Notion 数据库
- 不在 inbox.md 中写入任何内容
- 代码实现完成后提交 git commit，描述本次改动
```

### 9.2 Planner CLAUDE.md

```markdown
# 角色定义
你是一个项目规划 Agent，负责将模糊需求分解为结构化的可执行任务列表。
你拥有 Notion MCP 写权限，但只在收到确认信号后才写入。

# 工作流程
1. 读取 inbox.md 中的需求描述
2. 读取 WORKFLOW.md 了解整体工作流规范
3. 将需求拆解为 Task 列表，写入 outbox.md（等待确认）
4. 收到 inbox.md 中的 CONFIRMED 信号后，正式写入 Notion

# 任务拆解规范
- 每个 Task = 一次 Executor CC 不被打断能完成的工作量
- 只写"完成后能做什么"，不写技术实现路径
- 有依赖关系的任务必须设置 blocked_by
- 拆解粒度参考：小功能（1个task）/ 中等功能（2-3个task）/ 完整应用（3-5个milestone各含若干task）

# outbox.md 写入规范（确认前）
输出拆解方案，格式如下：

# Status
TASK_PROGRESS:等待确认

# Summary
[对拆解方案的整体描述]

# Plan
[Task 列表，每个 Task 包含：名称、描述、task_type、依赖]
```

---

## 十、WORKFLOW.md 模板

```markdown
# Claude Brain 工作流说明

## 系统概述
这是一个个人异步任务自动化系统。
- Notion：任务输入和进度追踪
- Brain daemon：任务调度（Python 常驻进程，运行于 Mac Mini）
- Claude Code：任务执行

## Notion 数据库结构
[详见技术文档第五章]

## 任务类型说明
- planner：将需求拆解为 Task 列表，有 Notion 写权限
- executor：执行具体开发任务，无 Notion 写权限

## 任务拆解规范
[详见 Planner CLAUDE.md 中的规范]

## 重要约束
- 同一 project 的任务串行执行
- 任务超时上限 2 小时
- 所有通信通过 inbox.md / outbox.md 文件进行
```

---

## 十一、MVP 实现边界（Phase 1）

### 包含

- Brain daemon 主循环（定时轮询 Notion + outbox）
- SQLite 状态管理
- Workspace 准备（clone / pull）
- Executor CC 启动和管理
- outbox 格式校验
- Notion 状态和日志（Running / Done / Blocked / Timeout）
- Notion execution_log append
- Watchdog 超时检测

### 不包含（后续迭代）

- Planner CC 及确认流程
- Telegram / IM 通知（后续迭代再接入）
- workspace TTL 自动清理
- 子任务结构化展示
- per-project 串行锁以外的并发策略

---

## 十二、后续迭代方向

| Phase | 功能 |
|---|---|
| Phase 2 | Planner CC + Notion 写入确认流程 |
| Phase 3 | Telegram / IM 接入（手动触发 + 结果推送） |
| Phase 4 | workspace TTL 自动清理 |
| Phase 5 | Notion 子任务结构化（替代纯日志方案） |
| Phase 6 | 活跃时间窗口配置（仅在指定时段执行任务） |