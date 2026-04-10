# 飞书设置

## 创建飞书应用

1. 前往 https://open.feishu.cn/app → 创建企业自建应用
2. 启用「机器人」能力
3. 订阅事件 `im.message.receive_v1`，接收方式选「使用长连接接收事件」
4. 添加权限：`im:message`、`im:message:send_as_bot`、`im:chat`
5. 发布应用版本

## 配置

```bash
ccbrain config feishu     # 引导填入 App ID 和 App Secret
ccbrain restart
```

## lark-cli（可选，增强 CC 能力）

安装后 CC 可操作飞书：发消息、查日历、管理文档、多维表格等。

```bash
ccbrain config lark-cli
```

## 命令

| 命令 | 说明 |
|------|------|
| `/btw <任务>` | 后台执行任务（不阻塞当前对话，最多 3 个并发） |
| `/model` | 查看当前模型和可用列表 |
| `/model switch <name>` | 切换模型（opus/sonnet/haiku/default） |
| `/usage` | 查看查询次数和累计费用 |
| `/status` | 查看 CC 连接状态、模型、费用 |
| `/reset` | 重置对话 session |
| `/help` | 帮助 |

## 消息处理

- 命令即时响应，不受 CC 执行阻塞
- 普通消息线性排队处理（同一会话内）
- 每个飞书群/私聊自动创建独立 workspace
- Session 空闲超时归档，下次对话自动 resume 恢复上下文
- 流式卡片输出：占位卡片 → 每 2 秒更新 → 最终结果

## Notion 集成（v0.4+）

飞书对话中可直接操作 Notion：

- 查询/创建 Project 和 Task
- 更新任务状态
- 需要先完成 Notion 配置：`ccbrain config notion`

## 安全

配置授权用户（空列表 = 不限制）：

```yaml
# ~/.ccbrain/config.yaml
feishu:
  allowed_users:
    - "ou_你的open_id"    # 从 ccbrain logs feishu 中获取
```

## 群聊

Bot 在群聊中只响应 @bot 的消息。私聊中所有消息都会处理。
