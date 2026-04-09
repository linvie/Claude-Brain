# Claude Brain

个人异步任务自动化系统 — 在 Notion 写需求，Brain 自动调度 Claude Code 执行，结果写回 Notion。

晚上在 Notion 写任务，早上查看执行结果。你只需要操作 Notion，不需要碰代码或终端。

```
Notion（写需求）→ Brain（自动调度）→ Claude Code（执行）→ Notion（查看结果）
```

## 功能概览

- **Planner**：需求拆解 — 描述你想要什么，CC 自动拆解为可执行的子任务
- **Executor**：代码实现 — 在隔离 workspace 中完成编码、测试、提交
- **Tester**：测试环境 — CC 生成启动/停止脚本，Brain 管理服务生命周期，通过 Notion 状态控制开关
- 同 project 串行、跨 project 并行，支持任务依赖
- 超时保护（2h）、进程健康检查、崩溃自动标记
- 执行日志自动写回 Notion

## 快速开始

### 前置要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)（`claude` 命令可用）

### 1. 安装

```bash
git clone <repo-url> && cd Claude-Brain
uv tool install -e .   # 注册 ccbrain 全局命令
```

### 2. 交互式配置

```bash
ccbrain init
```

自动创建 `~/.ccbrain/` 数据目录（config、logs、state.db、workspaces），引导完成配置。

配置向导会引导你完成所有设置：

- **Notion 任务流**（可选）：异步任务自动化，在 Notion 写需求、查看结果
- **飞书对话流**（可选）：实时对话，在飞书直接和 CC 对话

每个功能独立开关，未配置的自动跳过。

<details>
<summary>Notion 额外步骤（如启用）</summary>

1. 前往 https://www.notion.com/my-integrations → **New integration** → 获取 Token
2. 在向导中填入 Token
3. 在 Claude Code 中运行 `/brain-init` 自动创建数据库（或手动创建，见[手动创建 Notion 数据库](#手动创建-notion-数据库)）
4. 配置 Notion MCP（Planner CC 需要）：

```bash
claude mcp add notion --transport stdio --scope user \
  -e NOTION_TOKEN=<你的token> \
  -- npx -y @notionhq/notion-mcp-server
```

</details>

<details>
<summary>飞书额外步骤（如启用）</summary>

1. 前往 https://open.feishu.cn/app → 创建企业自建应用
2. 启用「机器人」能力
3. 订阅事件 `im.message.receive_v1`，接收方式选「使用长连接接收事件」
4. 添加权限：`im:message`、`im:message:send_as_bot`
5. 发布应用版本
6. 在向导中填入 App ID 和 App Secret

</details>

### 3. 启动

```bash
ccbrain install   # 首次：注册 launchd 服务并启动
ccbrain start     # 后续：启动服务
```

## 使用指南

### 直接执行（简单任务）

适合目标明确的任务 — bug fix、小功能、重构等。

1. 在 Project 数据库创建项目，填写 `project_name`、`repo_url`、`status = Active`
2. 在 Task 数据库创建任务：
   - `task_type` = `executor`
   - `description`：详细说明要做什么
   - `status` = `Ready`（Brain 下一轮自动拾取）

### 先规划再执行（复杂任务）

适合需求模糊或需要拆解的大任务。

1. 创建 Task，`task_type` = `planner`，描述你想要什么
2. Planner CC 自动拆解为子任务（`status = Pending`）
3. 审阅后将子任务改为 `Ready`

### 测试环境（Tester）

开发完成后，用 tester 任务启动测试环境。

**首次使用**：创建 Task，`task_type` = `tester`，`status` = `Ready`。CC 分析项目并生成 `test_start.sh` / `test_stop.sh`。

**启动测试**：将 tester 任务设为 `Ready`，Brain 直接运行脚本（不再调用 CC）。

**停止测试**：将 tester 任务改为 `Done`，Brain 自动停止脚本进程。

**重新测试**：再次设为 `Ready`，Brain 重启脚本。

> 测试运行期间占用 project 串行锁，需先停止测试才能执行其他任务。Tester 脚本不受 2 小时超时限制。

### 任务生命周期

```
Pending  →（手动改为 Ready）→  Ready
Ready    →（Brain 自动拾取）→  Running
Running  →（CC 完成）       →  Done
Running  →（CC 阻塞）       →  Blocked
Running  →（超时 2h）       →  Timeout
```

### 调度规则

- 跨 project 并行，同 project 串行（避免 workspace 冲突）
- 最大并发数：`config.yaml` → `scheduler.max_concurrent`（默认 3）
- `blocked_by` 中的任务全部 Done 后才会拾取
- 执行结果自动写入 Task 的 `execution_log`，代码变更在 `~/brain-workspaces/<project>/`

## 运行与管理

所有命令在任何目录下均可运行。数据存储在 `~/.ccbrain/`。

### 前台运行（调试用）

```bash
ccbrain run    # Ctrl+C 停止
```

### 后台运行（推荐）

```bash
ccbrain install   # 一次性安装（注册 launchd 服务并启动）
```

日常管理：

```bash
ccbrain start     # 启动服务（后台常驻，崩溃自动重启）
ccbrain stop      # 停止服务（优雅关闭）
ccbrain restart   # 重启
ccbrain status    # 查看运行状态（PID、运行时长）
ccbrain logs      # tail -f 主日志
ccbrain logs cc   # tail -f CC 日志
ccbrain uninstall # 卸载服务
```

### 日志

`logs/` 目录下 7 个分类日志，所有日志同时输出到终端（`[模块名]` 标识来源）：

| 文件 | 内容 |
|---|---|
| `brain.log` | 全量日志（所有模块的汇总） |
| `scheduler.log` | v1 任务生命周期（分发、完成、阻塞、超时） |
| `cc.log` | CC 进程事件（启动、退出、输出） |
| `notion.log` | Notion API 调用记录 |
| `feishu.log` | v2 飞书消息收发 |
| `session.log` | v2 Session 生命周期（创建、复用、过期） |
| `memory.log` | v2 记忆存取（添加、检索、提取） |

## 参考

### Notion 数据库字段

<details>
<summary>Project 数据库</summary>

| 属性名 | 类型 | 说明 |
|---|---|---|
| project_name | Title | 项目名称 |
| project_type | Select | `new` / `existing` |
| repo_url | URL | GitHub 仓库地址（`existing` 必填） |
| status | Select | `Active` / `Paused` / `Archived` |
| description | Text | 项目背景描述 |

</details>

<details>
<summary>Task 数据库</summary>

| 属性名 | 类型 | 说明 |
|---|---|---|
| task_name | Title | 任务名称 |
| description | Text | 任务描述（2-5 句，上限 2000 字符） |
| task_type | Select | `planner` / `executor` / `tester` |
| project | Relation | 关联到 Project 数据库 |
| blocked_by | Relation (self) | 依赖的前置任务 |
| status | Select | `Pending` / `Ready` / `Running` / `Done` / `Blocked` / `Timeout` |
| priority | Select | `High` / `Normal` / `Low` |
| execution_log | Text | 系统自动写入，不要手动编辑 |

</details>

### 手动创建 Notion 数据库

<details>
<summary>展开</summary>

如果不使用 `/brain-init` 自动配置，按以下步骤手动创建。

1. 在 Notion 创建页面 `Claude Brain`，在其中创建上述两个数据库
2. Share 给你的 integration
3. 获取数据库 ID（URL 中 32 位十六进制字符串）填入 `config.yaml`

```yaml
notion:
  token: "ntn_你的token"
  project_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  task_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Planner CC 需要 Notion MCP：

```bash
claude mcp add notion --transport stdio --scope user \
  -e NOTION_TOKEN=<你的token> \
  -- npx -y @notionhq/notion-mcp-server
```

</details>

### 项目结构

<details>
<summary>展开</summary>

```
claude-brain/
├── brain/                     # 核心包
│   ├── main.py                # 薄主循环，委托 core/ 模块
│   ├── config.py              # 配置加载，派生常量
│   ├── infra/                 # 基础设施层
│   │   ├── db.py              # SQLite schema、连接、查询辅助
│   │   └── logger.py          # 分类日志初始化（4 个 logger）
│   ├── integrations/          # 外部服务集成层
│   │   └── notion.py          # Notion REST API 客户端
│   ├── core/                  # 核心业务层
│   │   ├── dispatcher.py      # 任务分发
│   │   ├── watchdog.py        # 超时检测 + 进程健康检查
│   │   ├── outbox.py          # outbox 轮询与结果处理
│   │   ├── tester.py          # Tester 生命周期管理
│   │   ├── protocol.py        # inbox/outbox JSON 格式定义
│   │   └── process.py         # CC 子进程 + 测试脚本管理
│   ├── workspace/             # Workspace 管理层
│   │   ├── manager.py         # git clone/pull/init
│   │   └── setup.py           # 模板安装 + 上下文注入
│   ├── channels/              # v2: Channel adapter 层
│   │   ├── base.py            # 抽象接口 + 标准消息格式
│   │   └── feishu/            # 飞书实现（WebSocket + API）
│   ├── executor/              # v2: CC SDK 执行层
│   │   └── cc.py              # Claude Agent SDK 封装
│   ├── session/               # v2: Session 生命周期
│   │   └── manager.py         # channel→session 映射 + 过期管理
│   └── memory/                # v2: Brain-owned 记忆系统
│       ├── store.py           # SQLite CRUD
│       ├── retriever.py       # 检索 + context 组装
│       └── extractor.py       # 从 CC 输出提取记忆
├── templates/                 # CC 角色模板
│   ├── planner/               # Planner CC
│   ├── executor/              # Executor CC
│   ├── tester/                # Tester CC（脚本生成）
│   └── shared/                # 共享文件（WORKFLOW.md、OUTBOX_FORMAT.md 等）
├── brain.sh                   # 服务管理脚本（install/start/stop/status/logs）
├── config.example.yaml        # 配置模板
├── pyproject.toml             # uv 项目定义
├── .claude/
│   ├── skills/brain-init/     # /brain-init 自动配置命令
│   └── settings.json          # 项目级权限配置
├── logs/                      # 运行日志
└── state.db                   # SQLite 状态（运行时生成）
```

</details>
