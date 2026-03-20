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
| `brain/main.py` | 主循环、任务分发、watchdog、outbox 处理 |
| `brain/config.py` | 配置加载，导出 CONFIG 和派生常量 |
| `brain/db.py` | SQLite schema、连接工厂、查询辅助函数 |
| `brain/notion.py` | Notion REST API 客户端 + wrapper 函数 |
| `brain/protocol.py` | inbox/outbox JSON 通信协议 |
| `brain/workspace.py` | workspace git 管理 + 模板安装 |
| `brain/process.py` | CC 子进程启动 |
| `brain/logger.py` | 分类日志初始化 |
| `config.yaml` | 运行时配置（轮询间隔、超时、Notion token、角色权限） |
| `templates/` | CC 角色模板（planner/、executor/、shared/） |
| `state.db` | SQLite 运行时状态（task_runs + workspaces 表） |
| `Claude Brain — 技术设计文档.md` | 完整技术设计文档，架构决策的权威来源 |

## 技术栈

- Python 3.12+
- SQLite（状态管理）
- PyYAML（配置解析）
- requests（Notion REST API 调用）
- Claude Code CLI（`claude --print`，作为子进程启动）
- Notion MCP（`@notionhq/notion-mcp-server`，Planner CC 使用）

## CC 角色与权限

权限通过 CLI 参数 `--allowedTools` / `--disallowedTools` 硬性控制，不依赖 prompt 约束。配置集中在 `config.yaml` 的 `roles` 字段。

- **Planner CC**：有 Notion 写权限，无 Bash；负责需求拆解
- **Executor CC**：有完整文件和 Shell 工具，无 Notion 权限；负责代码实现

## 通信协议

Brain 与 CC 通过 workspace 中的 JSON 文件通信：
- `inbox.json`：Brain 写入任务描述，CC 读取
- `outbox.json`：CC 写入执行结果，Brain 轮询读取
- Status token：`TASK_DONE` / `TASK_BLOCKED` / `TASK_PROGRESS`

## 开发规范

- Brain 是确定性调度器，不包含业务推理逻辑
- 同一 project 的任务串行执行（per-project 锁）
- 任务超时上限 2 小时
- outbox.json 格式必须严格校验，不通过则标记为格式异常
- 当前处于 Phase 1 MVP：不含 Telegram 通知、workspace TTL 清理
