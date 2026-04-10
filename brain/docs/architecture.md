# 架构

## 双工作流

```
v1（Notion 任务流）：Notion → Brain 轮询 → CC CLI 子进程 → 结果写回 Notion
v2（飞书对话流）：飞书消息 → Brain WebSocket → CC SDK 会话 → 卡片回飞书
```

共享：asyncio 事件循环、SQLite 数据库、日志体系。

## 源码结构

```
brain/
├── main.py               # asyncio 主循环（v1 轮询 + v2 WebSocket 并行）
├── cli.py                # ccbrain 命令行入口
├── config.py             # 配置加载（~/.ccbrain/config.yaml）
├── setup.py              # 交互式配置向导
├── infra/                # 基础设施
│   ├── db.py             # SQLite schema + 连接
│   ├── logger.py         # 7 个分类 logger
│   └── feishu_notify.py  # 飞书通知（v1/v2 共用）
├── channels/             # Channel adapter
│   ├── base.py           # 抽象接口 + 标准消息格式
│   └── feishu/           # 飞书 WebSocket + API 客户端
├── executor/             # CC 执行器
│   └── cc.py             # Claude Agent SDK（持久会话 + 流式）
├── session/              # Session + Workspace 管理
│   └── manager.py        # per-channel workspace、session 生命周期
├── memory/               # 记忆系统
│   ├── store.py          # SQLite CRUD
│   ├── retriever.py      # 检索 + context 组装
│   └── extractor.py      # 从 CC 输出提取事实
├── core/                 # v1 Notion 任务调度
│   ├── dispatcher.py     # 任务分发
│   ├── watchdog.py       # 超时检测
│   ├── outbox.py         # 结果轮询 + 飞书通知
│   ├── protocol.py       # inbox/outbox JSON 格式
│   └── process.py        # CC 子进程管理
├── integrations/         # 外部服务
│   └── notion.py         # Notion REST API
├── workspace/            # v1 workspace 模板
│   ├── manager.py        # git clone/pull
│   └── setup.py          # 模板安装 + 上下文注入
├── data/                 # 打包资源（随 wheel 分发）
│   ├── config.example.yaml
│   └── template/         # v2 workspace 模板
└── docs/                 # 文档
```

## 分层依赖

```
config.py              ← 无依赖（基础层）
    ↑
infra/                 ← 只依赖 config
integrations/          ← 只依赖 config
    ↑
workspace/             ← 依赖 config + infra
core/                  ← 依赖 infra + integrations + workspace
channels/              ← 依赖 infra
executor/              ← 依赖 infra
session/               ← 依赖 config + infra
memory/                ← 依赖 infra
    ↑
main.py                ← 编排层，汇合所有模块
```

**约束**：v1 模块（core/、integrations/、workspace/）和 v2 模块（channels/、executor/、session/、memory/）互不依赖。违规由 pre-commit hook 检测。

## v2 消息流

```
飞书消息 → FeishuAdapter → _dispatch_message()
  ├─ /help /reset /status /model /usage → 即时响应
  ├─ /btw <task>  → 即时回复 + 后台 CC
  └─ 普通消息     → per-channel 队列 → _handle_chat()
       → 占位卡片（"思考中..."）
       → 记忆检索 + system_append（chat_id + Notion context）
       → CC SDK execute（流式回调每 2s 更新卡片）
       → 最终结果更新卡片
       → 提取记忆
```

## 日志

| 文件 | 内容 |
|------|------|
| `brain.log` | 全量汇总 |
| `feishu.log` | 飞书消息收发 |
| `cc.log` | CC 进程事件 |
| `session.log` | Session 生命周期 |
| `memory.log` | 记忆存取 |
| `scheduler.log` | Notion 任务调度 |
| `notion.log` | Notion API 调用 |
