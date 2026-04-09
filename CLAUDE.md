# Claude Brain

个人异步任务自动化系统 — Notion 输入 → Brain 调度 → Claude Code 执行。v2 新增飞书实时对话能力。

## 项目架构

系统有两套并行运行的工作流：

- **v1（Notion 任务流）**：Notion 写需求 → Brain 轮询分发 → CC 子进程执行 → 结果写回 Notion
- **v2（飞书对话流）**：飞书发消息 → Brain 接收 → CC SDK 执行 → 结果回飞书

两套工作流共享同一个 asyncio 事件循环、同一个 SQLite 数据库、同一套日志体系。

职责边界：Brain 管"什么时候做、做完了告诉谁" + 记忆管理；CC 管"怎么做"。

## 关键文件

### v1（Notion 任务流）

| 文件 | 用途 |
|---|---|
| `brain/core/dispatcher.py` | 任务分发（workspace 准备 → inbox 构建 → CC 启动） |
| `brain/core/watchdog.py` | 超时检测 + 进程健康检查 |
| `brain/core/outbox.py` | outbox.json 轮询与结果处理 |
| `brain/core/protocol.py` | inbox/outbox JSON 格式定义 |
| `brain/core/process.py` | CC 子进程启动 |
| `brain/integrations/notion.py` | Notion REST API 客户端 |
| `brain/workspace/manager.py` | workspace git clone/pull/init |
| `brain/workspace/setup.py` | 模板安装 + 上下文注入 |
| `templates/` | CC 角色模板（planner/、executor/、shared/） |

### v2（飞书对话流）

| 文件 | 用途 |
|---|---|
| `brain/channels/base.py` | Channel 抽象接口 + 标准消息格式 |
| `brain/channels/feishu/adapter.py` | 飞书 WebSocket adapter |
| `brain/channels/feishu/client.py` | 飞书 API 客户端（发送/回复/编辑消息） |
| `brain/executor/cc.py` | Claude Agent SDK 封装（query + resume） |
| `brain/session/manager.py` | Session 生命周期（channel→session 映射 + 过期管理） |
| `brain/memory/store.py` | 记忆 SQLite CRUD |
| `brain/memory/retriever.py` | 记忆检索 + context 组装 |
| `brain/memory/extractor.py` | 从 CC 输出提取记忆（Phase A: 规则匹配） |

### 共享

| 文件 | 用途 |
|---|---|
| `brain/main.py` | asyncio 主循环，编排 v1 轮询 + v2 channel adapter |
| `brain/config.py` | 配置加载，导出 CONFIG 和派生常量 |
| `brain/infra/db.py` | SQLite schema、连接工厂（v1 + v2 表） |
| `brain/infra/logger.py` | 分类日志初始化（4 个 logger） |
| `config.yaml` | 运行时配置 |
| `state.db` | SQLite 运行时状态 |

## 分层架构

```
config.py              ← 无 brain 内部依赖（基础层）
    ↑
infra/                 ← 只依赖 config（基础设施层）
integrations/          ← 只依赖 config（外部服务层）
    ↑
workspace/             ← 依赖 config、infra/logger（workspace 层）
core/protocol.py       ← 无依赖（纯数据格式）
channels/base.py       ← 无 brain 内部依赖（接口定义）
    ↑
core/dispatcher.py     ← 依赖 infra + integrations + workspace + protocol
core/watchdog.py       ← 依赖 infra + integrations
core/outbox.py         ← 依赖 infra + integrations + protocol
channels/feishu/       ← 依赖 channels/base + infra/logger
executor/              ← 依赖 infra/logger
session/               ← 依赖 config + infra/logger
memory/                ← 依赖 infra/logger
    ↑
main.py                ← 依赖 core + channels + executor + session + memory
```

约束：v1 模块（core/、integrations/、workspace/）和 v2 模块（channels/、executor/、session/、memory/）互不依赖，只在 main.py 编排层汇合。

## 技术栈

- Python 3.12+
- SQLite（状态管理）
- PyYAML（配置解析）
- requests（Notion REST API 调用）
- lark-oapi（飞书 SDK，WebSocket 长连接 + 消息 API）
- claude-agent-sdk（Claude Code SDK，v2 执行层）
- Claude Code CLI（`claude --print`，v1 执行层）
- Notion MCP（`@notionhq/notion-mcp-server`，Planner CC 使用）

## v1: CC 角色与权限

权限通过 CLI 参数 `--allowedTools` / `--disallowedTools` 硬性控制，不依赖 prompt 约束。配置集中在 `config.yaml` 的 `roles` 字段。

- **Planner CC**：有 Notion 写权限，无 Bash；负责需求拆解
- **Executor CC**：有完整文件和 Shell 工具，无 Notion 权限；负责代码实现

## v1: 通信协议

Brain 与 CC 通过 workspace 中的 JSON 文件通信：
- `inbox.json`：Brain 写入完整任务上下文，CC 读取
- `outbox.json`：CC 写入执行结果，Brain 轮询读取
- Status token：`TASK_DONE` / `TASK_BLOCKED` / `TASK_PROGRESS`

## v2: 消息流

```
飞书消息 → FeishuAdapter → main._handle_channel_message()
  → 发送"思考中..."占位
  → 检索/创建 session
  → 组装记忆 context
  → executor.cc.execute()（Claude Agent SDK）
  → 编辑占位消息为结果
  → 提取记忆存入 DB
```

## v2: 记忆系统

Brain-owned 记忆系统，独立于 CC 的 per-project 记忆。

- 存储：SQLite `memories` 表
- 检索：关键词匹配 content + tags，按 importance + recency 排序
- 注入：通过 `--append-system-prompt` 将相关记忆注入 CC context
- 提取：Phase A 用规则匹配从 CC 输出提取事实（后续升级为 LLM 提取或 MCP tools）

## 远程开发模式

通过 `config.yaml` 的 `remote` 配置段启用，支持通过 Tailscale 等组网方案从远程设备访问。

## 开发规范

- Brain 是确定性调度器，不包含业务推理逻辑
- v1: 同 project 串行，跨 project 并行；v2: per-channel 串行处理
- 最大并发 CC 进程数由 `config.yaml` 的 `scheduler.max_concurrent` 控制
- v1 和 v2 模块互不依赖，只在 main.py 汇合
- Channel adapter 是纯 I/O 层，不含业务逻辑
- CC 不知道 Brain 的存在（workspace + system prompt + 消息）
