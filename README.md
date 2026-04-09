# CCBrain

个人 AI daemon — 后台常驻，调度 Claude Code 执行任务。两种使用模式：

- **飞书对话**：在飞书聊天中直接和 AI 对话，实时执行
- **Notion 任务**：在 Notion 写需求，后台异步执行，结果写回 Notion

两种模式独立开关，可同时启用，共享同一个 daemon 进程。

## 快速开始

### 前置要求

- Python 3.12+、[uv](https://docs.astral.sh/uv/)、[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)

### 安装

```bash
# 远程安装（任何设备）
uv tool install git+https://github.com/linvie/Claude-Brain.git

# 或本地开发（可编辑，代码改了立即生效）
git clone https://github.com/linvie/Claude-Brain.git && cd Claude-Brain
uv tool install -e .
```

### 配置与启动

```bash
ccbrain init      # 交互式配置向导（创建 ~/.ccbrain/，引导开启飞书/Notion）
ccbrain install   # 注册后台服务并启动
```

升级：`uv tool upgrade ccbrain`

---

## 飞书对话模式

在飞书中和 AI 实时对话。CC 拥有完整工具能力（搜索、浏览器、代码执行、文件操作等）。

### 设置

`ccbrain init` 时启用飞书，填入 App ID 和 App Secret。需要先在飞书开发者后台创建应用：

1. 前往 https://open.feishu.cn/app → 创建企业自建应用
2. 启用「机器人」能力
3. 订阅事件 `im.message.receive_v1`，接收方式选「使用长连接接收事件」
4. 添加权限：`im:message`、`im:message:send_as_bot`
5. 发布应用版本

### 飞书 CLI（可选，增强 CC 工具能力）

```bash
ccbrain config lark-cli
```

安装后 CC 可直接操作飞书：发消息、查日历、管理文档、操作多维表格等 20 个 skill。

### 使用

直接在飞书给 Bot 发消息即可对话。支持以下命令：

| 命令 | 说明 |
|---|---|
| `/btw <任务>` | 后台执行任务（不阻塞当前对话） |
| `/reset` | 重置对话 session |
| `/status` | 查看当前 session 状态 |
| `/help` | 显示可用命令 |

- 命令即时响应，不受 CC 执行阻塞
- 普通消息线性排队处理（同一会话内）
- 每个飞书群/私聊自动创建独立 workspace
- Session 自动管理：空闲超时归档，下次对话自动恢复

### 安全

在 `~/.ccbrain/config.yaml` 配置授权用户：

```yaml
feishu:
  allowed_users:
    - "ou_你的open_id"    # 从日志中获取
```

空列表 = 不限制。建议配置后重启服务。

---

## Notion 任务模式

在 Notion 写需求，Brain 自动调度 CC 执行，结果写回 Notion。适合异步任务：晚上写任务，早上看结果。

### 设置

`ccbrain init` 时启用 Notion，填入 Integration Token。然后在 Claude Code 中运行 `/brain-init` 自动创建数据库。

Planner CC 需要 Notion MCP：

```bash
claude mcp add notion --transport stdio --scope user \
  -e NOTION_TOKEN=<你的token> \
  -- npx -y @notionhq/notion-mcp-server
```

### 使用

**直接执行**（目标明确的任务）：

1. 在 Project 数据库创建项目（`project_name`、`repo_url`、`status = Active`）
2. 创建 Task：`task_type = executor`，写 description，`status = Ready`
3. Brain 自动拾取并执行

**先规划再执行**（复杂任务）：

1. 创建 Task：`task_type = planner`，描述需求
2. Planner CC 自动拆解为子任务
3. 审阅后将子任务改为 `Ready`

**测试环境**：

1. 创建 Task：`task_type = tester`，`status = Ready`
2. CC 生成启动/停止脚本，Brain 管理服务生命周期
3. 改为 `Done` 停止，改为 `Ready` 重新启动

### 任务生命周期

```
Pending → Ready → Running → Done / Blocked / Timeout
```

### 调度规则

- 跨 project 并行，同 project 串行
- 最大并发数由 `scheduler.max_concurrent` 控制（默认 3）
- `blocked_by` 中的任务全部 Done 后才拾取
- 执行日志自动写入 Task 的 `execution_log`

---

## CLI 命令

所有命令全局可用，数据存储在 `~/.ccbrain/`。

```bash
ccbrain init              # 交互式配置向导
ccbrain config <sub>      # 配置管理（show/edit/feishu/notion/lark-cli）
ccbrain install           # 注册后台服务并启动
ccbrain uninstall         # 卸载服务（不删除数据）
ccbrain start             # 启动
ccbrain stop              # 停止
ccbrain restart           # 重启
ccbrain status            # 查看运行状态
ccbrain run               # 前台运行（调试用）
ccbrain logs [name]       # 查看日志
ccbrain --version         # 版本号
```

日志文件（`~/.ccbrain/logs/`）：

| 文件 | 内容 |
|---|---|
| `brain.log` | 全量汇总 |
| `feishu.log` | 飞书消息收发 |
| `cc.log` | CC 进程事件 |
| `session.log` | Session 生命周期 |
| `memory.log` | 记忆存取 |
| `scheduler.log` | Notion 任务调度 |
| `notion.log` | Notion API 调用 |

---

## 数据目录

```
~/.ccbrain/
├── config.yaml           # 运行时配置
├── state.db              # SQLite 状态
├── logs/                 # 日志文件
└── workspaces/           # per-channel workspace
    └── {chat_id}/        # 每个飞书群/私聊独立
        ├── CLAUDE.md     # CC 人设 + 指令
        └── .claude/      # CC 配置（MCP、权限等）
```

<details>
<summary>源码结构</summary>

```
brain/
├── main.py               # asyncio 主循环（飞书 + Notion 并行）
├── cli.py                # ccbrain 命令行入口
├── config.py             # 配置加载（~/.ccbrain/config.yaml）
├── setup.py              # 交互式配置向导
├── infra/                # 基础设施（SQLite、日志）
├── channels/             # Channel adapter（飞书等）
│   ├── base.py           # 抽象接口
│   └── feishu/           # 飞书 WebSocket + API
├── executor/             # CC 执行器（Claude Agent SDK）
├── session/              # Session + Workspace 管理
├── memory/               # 记忆系统
├── core/                 # Notion 任务调度（dispatcher、watchdog、outbox）
├── integrations/         # Notion API 客户端
└── workspace/            # Notion workspace 模板管理
```

</details>

<details>
<summary>Notion 数据库字段</summary>

**Project 数据库**

| 属性名 | 类型 | 说明 |
|---|---|---|
| project_name | Title | 项目名称 |
| project_type | Select | `new` / `existing` |
| repo_url | URL | GitHub 仓库地址 |
| status | Select | `Active` / `Paused` / `Archived` |
| description | Text | 项目背景描述 |

**Task 数据库**

| 属性名 | 类型 | 说明 |
|---|---|---|
| task_name | Title | 任务名称 |
| description | Text | 任务描述 |
| task_type | Select | `planner` / `executor` / `tester` |
| project | Relation | 关联到 Project 数据库 |
| blocked_by | Relation (self) | 依赖的前置任务 |
| status | Select | `Pending` / `Ready` / `Running` / `Done` / `Blocked` / `Timeout` |
| priority | Select | `High` / `Normal` / `Low` |
| execution_log | Text | 系统自动写入 |

</details>

<details>
<summary>手动创建 Notion 数据库</summary>

1. 在 Notion 创建页面 `Claude Brain`，创建上述两个数据库
2. Share 给你的 Integration
3. 获取数据库 ID 填入 `~/.ccbrain/config.yaml`

```yaml
notion:
  token: "ntn_你的token"
  project_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  task_db_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

</details>
