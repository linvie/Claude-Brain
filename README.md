# CCBrain

个人 AI daemon — 后台常驻，调度 Claude Code 执行任务。

- **飞书对话**：实时聊天，流式卡片输出，可操作 Notion
- **Notion 任务**：异步执行，Planner 拆解 + Executor 实现，结果自动回写
- **双向联动**：飞书中查/建 Notion 任务，Notion 任务完成自动通知飞书

## 安装

```bash
# 远程安装
uv tool install git+https://github.com/linvie/Claude-Brain.git

# 本地开发（可编辑）
git clone https://github.com/linvie/Claude-Brain.git && cd Claude-Brain
uv tool install -e .
```

前置要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)、[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)

## 配置与启动

```bash
ccbrain init        # 交互式配置（飞书/Notion 一站式引导）
ccbrain install     # 注册 launchd 后台服务并启动
```

升级：`uv tool upgrade ccbrain && ccbrain install && ccbrain restart`

## 飞书对话

直接给 Bot 发消息对话。CC 拥有完整工具能力。

| 命令 | 说明 |
|------|------|
| `/btw <任务>` | 后台执行（不阻塞对话） |
| `/model switch <name>` | 切换模型（opus/sonnet/haiku） |
| `/usage` | 查看用量和费用 |
| `/status` | Session 状态 |
| `/reset` | 重置对话 |
| `/help` | 帮助 |

详细设置 → [brain/docs/feishu.md](brain/docs/feishu.md)

## Notion 任务

在 Notion 写需求 → Brain 自动调度 → CC 执行 → 结果写回。

```
Pending → Ready → Running → Done / Blocked
```

- `task_type = planner`：需求拆解为子任务
- `task_type = executor`：代码实现
- `task_type = tester`：生成测试环境

详细设置 → [brain/docs/notion.md](brain/docs/notion.md)

## CLI

```bash
ccbrain init              # 配置向导
ccbrain config <sub>      # 配置管理（show/edit/feishu/notion/lark-cli）
ccbrain install           # 注册服务
ccbrain start / stop / restart
ccbrain status            # 运行状态
ccbrain logs [name]       # 查看日志（brain/feishu/cc/session/scheduler）
ccbrain run               # 前台运行（调试）
```

## 数据目录

```
~/.ccbrain/
├── config.yaml       # 配置
├── state.db          # 状态
├── logs/             # 日志
└── workspaces/       # per-channel workspace
```

## 文档

| 文档 | 内容 |
|------|------|
| [飞书设置](brain/docs/feishu.md) | 飞书应用创建、权限、lark-cli、安全配置 |
| [Notion 设置](brain/docs/notion.md) | 数据库字段、任务流程、调度规则 |
| [架构](brain/docs/architecture.md) | 源码结构、分层设计、v1/v2 工作流 |
