# Claude Brain 工作流说明

## 系统概述
这是一个个人异步任务自动化系统。
- Notion：任务输入和进度追踪
- Brain daemon：任务调度（Python 常驻进程，运行于 Mac Mini）
- Claude Code：任务执行

## Notion 数据库结构

### Project 数据库
| 字段名 | 类型 | 说明 |
|---|---|---|
| project_name | Title | 项目名称 |
| project_type | Select | `new` / `existing` |
| repo_url | URL | GitHub 仓库地址（existing 类型必填） |
| status | Select | `Active` / `Paused` / `Archived` |
| description | Text | 项目背景描述 |

### Task 数据库
| 字段名 | 类型 | 说明 |
|---|---|---|
| task_name | Title | 任务名称（一句话） |
| description | Text | 2-5句，说清楚要做什么、有什么约束 |
| task_type | Select | `planner` / `executor` |
| project | Relation | 关联到 Project 数据库 |
| blocked_by | Relation（self） | 依赖的前置任务 |
| status | Select | `Pending` / `Ready` / `Running` / `Done` / `Blocked` / `Timeout` |
| priority | Select | `High` / `Normal` / `Low` |
| scheduled_at | Date | 定时拾取时间。为空则不受时间约束；非空时 Pending 任务在该时间到达后自动被调度拾取 |
| execution_log | Text | 执行日志（时间戳 + 进度摘要） |

## 通信协议

Brain 与 CC 通过 workspace 中的 JSON 文件通信：

### inbox.json（Brain → CC，只读）
```json
{
  "task_id": "xxx",
  "task_type": "executor",
  "project_id": "yyy",
  "project_name": "项目名称",
  "task_name": "任务标题",
  "description": "任务描述",
  "priority": "Normal",
  "blocked_by": [],
  "context": {
    "project_description": "项目背景描述",
    "repo_url": "https://github.com/...",
    "related_tasks": [
      {"task_name": "任务A", "status": "Done", "summary": "摘要"},
      {"task_name": "任务B", "status": "Pending", "summary": ""}
    ]
  }
}
```

### outbox.json（CC → Brain）
状态令牌：`TASK_DONE` / `TASK_BLOCKED` / `TASK_PROGRESS`

详细格式参见 `OUTBOX_FORMAT.md`。

## 任务类型说明
- **planner**：将需求拆解为 Task 列表，有 Notion 写权限，无 Bash
- **executor**：执行具体开发任务，有完整文件和 Shell 工具，无 Notion 权限

## 调度规则
- 任务可被拾取的条件：(status=Ready) 或 (status=Pending 且 scheduled_at 非空且 scheduled_at ≤ 当前时间)
- 额外约束：blocked_by 中的所有前置任务必须 Done，且同 project 无运行中任务

## 重要约束
- 同一 project 的任务串行执行
- 任务超时上限 2 小时
- CC 启动后完全孤立，所有上下文在 inbox.json 中一次性提供
- 所有通信通过 inbox.json / outbox.json 文件进行
