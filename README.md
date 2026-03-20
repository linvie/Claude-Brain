# Claude Brain

Notion 输入 → Brain 调度 → Claude Code 执行的个人异步任务自动化系统。

晚上在 Notion 写任务 → 系统自动执行 → 早上查看 Notion 中的执行状态和日志。

## 前置要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python 包管理)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` 命令可用)
- Notion 账号

## 快速开始

提供两种配置方式，选择其一即可。

---

### 方式 A：自动配置（推荐）

在 Claude Code 中打开本项目，运行 slash command 一键完成 Notion 配置。

#### 1. 安装依赖并创建配置文件

```bash
uv sync
cp config.example.yaml config.yaml
```

#### 2. 创建 Notion Integration

1. 前往 https://www.notion.com/my-integrations
2. 点击 **New integration**，命名为 `Claude Brain`
3. **Capabilities** 中确保勾选了 Read/Update/Insert content
4. 复制生成的 **Internal Integration Secret**（以 `ntn_` 开头）

> **重要**：创建后需要在 Notion 中为 integration 授权访问空间。打开 Notion，进入你想要放置 Brain 数据库的页面，点击右上角 `...` → `Connections` → 搜索并添加 `Claude Brain`。

#### 3. 配置 Notion MCP

```bash
claude mcp add notion --transport stdio --scope user \
  -e NOTION_TOKEN=<你的_ntn_token> \
  -- npx -y @notionhq/notion-mcp-server
```

验证连接：

```bash
claude mcp list
# 应看到: notion: ... - ✓ Connected
```

#### 4. 一键初始化

在 Claude Code 中打开本项目目录，执行：

```
/brain-init
```

这会自动完成：
- 检索你的 Notion workspace 中已授权的页面
- 创建 Claude Brain 主页面
- 创建 Project 和 Task 数据库（含所有属性和选项）
- 将数据库 ID 写入 `config.yaml`

#### 5. 配置 Notion Token

将 token 填入 `config.yaml`：

```yaml
notion:
  token: "ntn_你的token"
```

#### 6. 验证

```bash
uv run python -m brain
```

---

### 方式 B：手动配置

如果你不使用 Claude Code，或希望完全控制配置过程。

#### 1. 安装依赖并创建配置文件

```bash
uv sync
cp config.example.yaml config.yaml
```

#### 2. 创建 Notion Integration

同方式 A 的步骤 2。

#### 3. 在 Notion 中创建数据库

创建一个页面命名为 `Claude Brain`，在其中创建以下两个数据库，并 Share 给你的 integration。

**Project 数据库**：

| 属性名 | 类型 | 说明 |
|---|---|---|
| project_name | Title | 项目名称 |
| project_type | Select | 选项：`new` / `existing` |
| repo_url | URL | GitHub 仓库地址（`existing` 类型必填） |
| status | Select | 选项：`Active` / `Paused` / `Archived` |
| description | Text | 项目背景描述 |

**Task 数据库**：

| 属性名 | 类型 | 说明 |
|---|---|---|
| task_name | Title | 任务名称（一句话） |
| description | Text | 任务描述（2-5句） |
| task_type | Select | 选项：`planner` / `executor` |
| project | Relation | 关联到 Project 数据库 |
| blocked_by | Relation (self) | 依赖的前置任务 |
| status | Select | 选项：`Pending` / `Ready` / `Running` / `Done` / `Blocked` / `Timeout` |
| priority | Select | 选项：`High` / `Normal` / `Low` |
| execution_log | Text | 执行日志（由系统自动写入） |

#### 4. 编辑 config.yaml

填入你的 Notion token 和数据库 ID：

```yaml
notion:
  token: "ntn_你的token"
  project_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  task_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

> **如何获取数据库 ID**：打开 Notion 数据库页面，URL 格式为 `https://www.notion.so/<workspace>/<database_id>?v=...`，其中 32 位十六进制字符串即为数据库 ID。

#### 5. 配置 Notion MCP（如使用 Claude Code）

Planner CC 需要通过 MCP 创建 Task，因此仍需配置：

```bash
claude mcp add notion --transport stdio --scope user \
  -e NOTION_TOKEN=<你的_ntn_token> \
  -- npx -y @notionhq/notion-mcp-server
```

#### 6. 验证

```bash
uv run python -m brain
```

---

## 使用方式

### 工作流程概述

```
你在 Notion 写需求 → Brain 自动拾取 → CC 执行 → 结果写回 Notion
```

你只需要操作 Notion，不需要碰代码或终端。

### 路径 A：直接执行（简单任务）

适合目标明确、不需要拆解的任务（bug fix、小功能、重构等）。

1. **创建 Project**（如果还没有）：在 Project 数据库新建一条，填写 `project_name`、`repo_url`（已有仓库）、`status = Active`
2. **创建 Task**：在 Task 数据库新建一条：
   - `task_name`：一句话描述（如 "add rate limiting to API"）
   - `description`：详细说明要做什么（2-5 句，上限 2000 字符）
   - `project`：关联到对应 Project
   - `task_type`：`executor`
   - `priority`：`High` / `Normal` / `Low`
   - `status`：设为 `Ready`，Brain 会在下一轮轮询时自动拾取

### 路径 B：先规划再执行（复杂任务）

适合需求模糊或需要拆解的大任务。

1. **创建 Project**（同上）
2. **创建 Planner Task**：
   - `task_type`：`planner`
   - `description`：描述你想要什么，不需要具体到实现细节
   - `status`：`Ready`
3. **Planner CC 执行**：Brain 拾取后，Planner CC 会通过 Notion MCP 在 Task 数据库中创建拆解出的子任务（`status = Pending`）
4. **审阅并启动**：你审阅 Planner 创建的子任务，满意后把它们的 `status` 改为 `Ready`

### Notion 字段说明

**Project 数据库**：

| 字段 | 作用 |
|---|---|
| `project_name` | 项目名称，传入 CC 作为上下文 |
| `repo_url` | 仓库地址，Brain 用它 clone workspace |
| `description` | 项目背景描述，传入 CC 作为上下文 |
| `status` | `Active` / `Paused` / `Archived` |

**Task 数据库**：

| 字段 | 作用 |
|---|---|
| `task_name` | 任务标题，传入 CC |
| `description` | 任务详细描述，传入 CC（上限 2000 字符） |
| `task_type` | `planner`（需求拆解）或 `executor`（代码实现） |
| `project` | 关联 Project，决定 workspace 和上下文 |
| `blocked_by` | 依赖的前置任务，未完成则不会被拾取 |
| `priority` | 影响拾取顺序（High > Normal > Low） |
| `status` | 任务状态（见下方生命周期） |
| `execution_log` | **系统自动写入**，不要手动编辑 |

### 任务生命周期

```
Pending  →（你手动改为 Ready）→  Ready
Ready    →（Brain 自动拾取）  →  Running
Running  →（CC 完成）         →  Done
Running  →（CC 阻塞）         →  Blocked
Running  →（超时 2h）         →  Timeout
```

### 并发与调度规则

- **不同 project 的任务可并行执行**，最大并发数由 `config.yaml` 的 `scheduler.max_concurrent` 控制（默认 3）
- **同一 project 的任务串行执行**（避免 workspace 冲突）
- `blocked_by` 中的任务全部 Done 后才会拾取
- 每轮都会检查运行中任务的状态 + 查询新的 Ready 任务

### 执行结果

- CC 完成后，摘要自动写入 Task 的 `execution_log` 字段
- 代码变更在 workspace 目录中（`~/brain-workspaces/<project>/`）

## 运行与管理

### 前台运行（调试用）

```bash
uv run python -m brain
```

`Ctrl+C` 停止。适合首次测试和排查问题。

### 后台运行（推荐：launchd）

创建 launchd plist 实现开机自启、崩溃自动重启：

```bash
cat > ~/Library/LaunchAgents/com.linvie.claude-brain.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.linvie.claude-brain</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/linvie.li/.local/bin/uv</string>
        <string>run</string>
        <string>python</string>
        <string>-m</string>
        <string>brain</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/linvie.li/code/linvie/Claude-Brain</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/linvie.li/code/linvie/Claude-Brain/logs/launchd.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/linvie.li/code/linvie/Claude-Brain/logs/launchd.stderr.log</string>
</dict>
</plist>
EOF
```

常用命令：

```bash
# 启动
launchctl load ~/Library/LaunchAgents/com.linvie.claude-brain.plist

# 停止
launchctl unload ~/Library/LaunchAgents/com.linvie.claude-brain.plist

# 查看状态
launchctl list | grep claude-brain

# 查看日志
tail -f logs/brain.log
tail -f logs/scheduler.log
```

## 项目结构

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
│   │   ├── protocol.py        # inbox/outbox JSON 格式定义
│   │   └── process.py         # CC 子进程启动
│   └── workspace/             # Workspace 管理层
│       ├── manager.py         # git clone/pull/init
│       └── setup.py           # 模板安装 + 上下文注入
├── templates/                 # CC 角色模板
│   ├── planner/               # Planner CC（CLAUDE.md、settings.json）
│   ├── executor/              # Executor CC
│   └── shared/                # 共享文件（WORKFLOW.md、OUTBOX_FORMAT.md、validate_outbox.py）
├── config.example.yaml        # 配置模板（复制为 config.yaml 使用）
├── pyproject.toml             # uv 项目定义
├── .claude/
│   ├── skills/brain-init/     # /brain-init 自动配置命令
│   └── settings.json          # 项目级权限配置
├── logs/                      # 运行日志（4 个分类文件）
└── state.db                   # SQLite 状态（运行时生成）
```

## 日志

运行时在 `logs/` 目录下生成 4 个分类日志：

| 文件 | 内容 |
|---|---|
| `brain.log` | 全量日志 |
| `scheduler.log` | 任务生命周期（分发、完成、阻塞、超时） |
| `cc.log` | CC 进程事件（启动、退出、输出） |
| `notion.log` | Notion API 调用记录 |
