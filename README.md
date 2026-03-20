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

#### 1. 安装依赖

```bash
uv sync
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
/init
```

这会自动完成：
- 检索你的 Notion workspace 中已授权的页面
- 创建 Claude Brain 主页面
- 创建 Project 和 Task 数据库（含所有属性和选项）
- 将数据库 ID 写入 `config.yaml`

#### 5. 验证

```bash
uv run python brain.py
```

---

### 方式 B：手动配置

如果你不使用 Claude Code，或希望完全控制配置过程。

#### 1. 安装依赖

```bash
uv sync
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

填入你的 Notion 数据库 ID：

```yaml
notion:
  project_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  task_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

> **如何获取数据库 ID**：打开 Notion 数据库页面，URL 格式为 `https://www.notion.so/<workspace>/<database_id>?v=...`，其中 32 位十六进制字符串即为数据库 ID。

#### 5. 配置 Notion MCP（如使用 Claude Code）

```bash
claude mcp add notion --transport stdio --scope user \
  -e NOTION_TOKEN=<你的_ntn_token> \
  -- npx -y @notionhq/notion-mcp-server
```

#### 6. 验证

```bash
uv run python brain.py
```

---

## 使用方式

### 创建任务

1. 在 Notion Project 数据库中创建一个项目，设置 `status = Active`
2. 在 Task 数据库中创建任务：
   - 关联到对应 Project
   - 填写 `description`（说清楚要做什么）
   - 设置 `task_type = executor`
   - 设置 `status = Ready`（Brain 会自动拾取）

### 任务生命周期

```
Pending  →（手动改为 Ready）→  Ready
Ready    →（Brain 拾取）    →  Running
Running  →（CC 完成）       →  Done
Running  →（CC 阻塞）       →  Blocked
Running  →（超时 2h）       →  Timeout
```

执行结果会自动写入 Task 的 `execution_log` 字段。

## 项目结构

```
claude-brain/
├── brain.py                  # Brain daemon 主程序
├── config.yaml               # 配置文件
├── pyproject.toml             # uv 项目定义
├── WORKFLOW.md                # 全局工作流（注入 CC 上下文）
├── templates/
│   ├── CLAUDE_executor.md     # Executor CC 角色模板
│   └── CLAUDE_planner.md      # Planner CC 角色模板
├── .claude/
│   ├── commands/init.md       # /init 自动配置命令
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
