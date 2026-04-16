# CCBrain

个人 AI daemon — 后台常驻，调度 Claude Code 执行任务。

- **飞书对话**：实时聊天，schema 2.0 流式卡片，交互卡片（按钮/表单），Typing 指示器
- **Notion 任务**：异步执行，Planner 拆解 + Executor 实现 + PR 工作流，结果自动回写
- **记忆系统**：FTS5 全文搜索 + Haiku LLM 提取 + 三层 Context Bridge + Daily Views
- **Heartbeat 心跳**：定时巡检 + 主动飞书通知，从"等人问"变"主动做事"
- **已有项目导入**：`existing` 项目自动迁移，AI 配置智能合并
- **双向联动**：飞书中查/建 Notion 任务，Notion 任务完成自动通知飞书
- **Lark 国际版**：支持飞书/Lark 双平台切换

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
ccbrain init        # 交互式配置（飞书/Notion 一站式引导，含权限和事件订阅指南）
ccbrain install     # 注册 launchd 后台服务并启动
```

升级：`uv tool upgrade ccbrain && ccbrain install && ccbrain restart`

## 飞书对话

直接给 Bot 发消息对话。CC 拥有完整工具能力 + Notion MCP（可读写 Notion）。

**特性：**
- Schema 2.0 卡片（宽屏、标题降级、原生表格、Typing 指示器、Footer 统计）
- 交互卡片（按钮/表单回调 → CC 自动处理，通过 `/ask` skill 构造）
- 持久 session（仅 `/reset` 时归档，平时永远 resume）
- 自动 context 压缩（70% 阈值）+ 三层错误自愈
- v1 任务完成/阻塞自动通知到飞书
- Lark 国际版支持（`platform: feishu` 或 `platform: lark`）

| 命令 | 说明 |
|------|------|
| `/btw <任务>` | 后台执行（不阻塞对话，最多 3 并发） |
| `/doctor` | 独立诊断系统状态（出错时使用） |
| `/model` / `/model switch <name>` | 查看/切换模型（opus/sonnet/haiku/default） |
| `/usage` | 查看用量和费用 |
| `/status` | Session 状态、CC 连接、模型 |
| `/reset` | 重置对话 session |
| `/help` | 帮助 |

详细设置 → [brain/docs/feishu.md](brain/docs/feishu.md)

## Notion 任务

在 Notion 写需求 → Brain 自动调度 → CC 执行 → 创建 PR → 结果写回。

```
Pending → Ready → Running → Done / Blocked
```

- `task_type = planner`：需求拆解为子任务
- `task_type = executor`：代码实现（feature branch + PR 工作流，不直推 main）
- `task_type = tester`：生成测试环境
- `project_type = existing`：自动创建迁移任务（clone/copy + AI 配置合并）

**Executor PR 工作流（v0.12+）**：Executor 在 feature branch 上工作，完成后推送并创建 PR。Brain 检测到 `pr_url` 后将链接写入 Notion 并通知飞书。

**Executor 内置 Skills**：`/qa`（质量检查）、`/review`（代码审查）、`/test-run`（跑测试）、`/migrate`（项目迁移）

详细设置 → [brain/docs/notion.md](brain/docs/notion.md)

## 记忆系统

跨 session 记忆，让 CC 了解历史上下文。

- **Raw Ledger**：JSONL 归档每次 CC 对话原始记录
- **LLM Extractor**：Haiku 从对话中提取结构化事实（替代正则）
- **FTS5 全文搜索**：trigram tokenizer 支持中英文检索
- **Context Bridge**：三层检索（高重要性 always-on + FTS5 相关性 + 近期 scope 过滤）+ Ebbinghaus 时间衰减
- **Daily Views**：每日自动生成对话摘要（每 6h 检查）

## Heartbeat 心跳

定时巡检系统状态，主动推送通知。

- 合并内建检查（`HEARTBEAT_SYSTEM.md`）和用户自定义（`HEARTBEAT.md`）
- 隔离 CC session 执行检查，结果含 `NO_ACTION` 时静默
- 否则自动推送飞书通知
- 默认间隔 3600 秒，可通过 `heartbeat.interval` 配置

## CLI

```bash
ccbrain init              # 配置向导（含飞书权限/事件引导）
ccbrain config <sub>      # 配置管理（show/edit/feishu/notion/lark-cli/reinit-workspace）
ccbrain install           # 注册服务（自动注入 shell PATH 到 launchd）
ccbrain start / stop / restart
ccbrain status            # 运行状态
ccbrain logs [name]       # 查看日志（brain/feishu/cc/session/scheduler/notion/memory）
ccbrain run               # 前台运行（调试）
```

## 数据目录

```
~/.ccbrain/
├── config.yaml         # 配置
├── state.db            # 状态 + 记忆
├── logs/               # 日志
├── memory/
│   ├── ledger/         # JSONL 原始对话归档
│   └── views/          # Daily Views 摘要（YYYY-MM-DD.md）
├── workspaces/         # per-channel workspace
└── HEARTBEAT.md        # 用户自定义心跳检查清单（可选）
```

## 文档

| 文档 | 内容 |
|------|------|
| [飞书设置](brain/docs/feishu.md) | 飞书应用创建、权限、事件订阅、lark-cli、安全配置 |
| [Notion 设置](brain/docs/notion.md) | 数据库字段、任务流程、PR 工作流、调度规则 |
| [架构](brain/docs/architecture.md) | 源码结构、分层设计、v1/v2 工作流 |

## Changelog (v0.7 — v0.15)

- **v0.15** — 飞书交互卡片（`card.action.trigger` 回调，`/ask` skill）+ Heartbeat 心跳机制（定时巡检 + 飞书通知）
- **v0.14** — `ccbrain init` 完善权限和事件订阅引导
- **v0.13** — Lark 国际版支持（`platform: feishu/lark` 双模式切换）
- **v0.12** — Executor PR 工作流 + V1 模板智能注入（`CCBRAIN_TEMPLATE_START/END` 标记合并）+ Notion API 重试 + outbox 竞态修复 + 记忆 bug 修复
- **v0.7 — v0.12** — 记忆系统 Phase B（Raw Ledger + Haiku LLM 提取 + FTS5 检索 + Context Bridge + Daily Views）
