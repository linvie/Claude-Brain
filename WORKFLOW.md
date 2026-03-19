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
| execution_log | Text | 执行日志（时间戳 + 进度摘要） |

## 任务类型说明
- planner：将需求拆解为 Task 列表，有 Notion 写权限
- executor：执行具体开发任务，无 Notion 写权限

## 重要约束
- 同一 project 的任务串行执行
- 任务超时上限 2 小时
- 所有通信通过 inbox.md / outbox.md 文件进行
