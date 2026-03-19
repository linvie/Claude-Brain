# Claude Brain

个人异步任务自动化系统 — Notion 输入 → Brain 调度 → Claude Code 执行。

## 项目架构

- **Notion**（输入层）：Project 数据库 + Task 数据库，用户在此定义需求
- **Brain daemon**（调度层）：Python 常驻进程，负责轮询 Notion、管理 CC 进程、收集结果
- **Claude Code**（执行层）：在隔离 workspace 中执行具体任务

职责边界：Notion 管"要什么"，CC 管"怎么做"，Brain 管"什么时候做、做完了告诉谁"。

## 关键文件

| 文件 | 用途 |
|---|---|
| `brain.py` | Brain daemon 主程序 |
| `config.yaml` | 配置（轮询间隔、超时、workspace 路径、角色权限） |
| `WORKFLOW.md` | 全局工作流描述，注入 Planner CC 上下文 |
| `templates/CLAUDE_executor.md` | Executor CC 的 CLAUDE.md 模板 |
| `templates/CLAUDE_planner.md` | Planner CC 的 CLAUDE.md 模板 |
| `state.db` | SQLite 运行时状态（task_runs + workspaces 表） |
| `Claude Brain — 技术设计文档.md` | 完整技术设计文档，架构决策的权威来源 |

## 技术栈

- Python 3.12+
- SQLite（状态管理）
- PyYAML（配置解析）
- Claude Code CLI（`claude --print`，作为子进程启动）
- Notion MCP（`@notionhq/notion-mcp-server`，已配置为全局 MCP）

## CC 角色与权限

权限通过 CLI 参数 `--allowedTools` / `--disallowedTools` 硬性控制，不依赖 prompt 约束。配置集中在 `config.yaml` 的 `roles` 字段。

- **Planner CC**：有 Notion 写权限，无 Bash；负责需求拆解
- **Executor CC**：有完整文件和 Shell 工具，无 Notion 权限；负责代码实现

## 通信协议

Brain 与 CC 通过 workspace 中的文件通信：
- `inbox.md`：Brain 写入任务描述，CC 读取
- `outbox.md`：CC 写入执行结果，Brain 轮询读取
- Status token：`TASK_DONE` / `TASK_BLOCKED:原因` / `TASK_PROGRESS:描述`

## 开发规范

- Brain 是确定性调度器，不包含业务推理逻辑
- 同一 project 的任务串行执行（per-project 锁）
- 任务超时上限 2 小时
- outbox.md 格式必须严格校验，不通过则标记为格式异常
- 当前处于 Phase 1 MVP：不含 Planner 确认流程、Telegram 通知、workspace TTL 清理

## 开发当前状态

- Brain daemon 骨架已完成，Notion MCP 调用部分为 TODO stub
- 下一步：接入 Notion MCP 实现 `fetch_ready_tasks_from_notion`、`notion_update_status`、`notion_append_log`
