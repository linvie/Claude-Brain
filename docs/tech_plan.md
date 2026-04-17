# 技术方案：基于 Prompt Caching 的 Session 生命周期优化

## 背景

飞书主 session 当前是无管理的长 session，JSONL 已累积 6.5MB，每条消息成本 $4-6。
根因：Anthropic prompt caching TTL 从 1 小时缩短到 5 分钟后，大部分消息到达时缓存已过期，
每次都付全价 cache write（context 越大越贵）。当前 `_LiveSession` 只有 idle disconnect 机制，
没有根据缓存状态主动压缩或重置 session 的能力。

## 核心事实

- Anthropic prompt caching TTL = 5 分钟（2026-03 起）
- TTL 从最后一次使用计时，命中自动续期
- 命中成本 = 原价 × 10%（节省 89%）
- 未命中 = 全价 cache write（context 越大越贵）
- Claude Agent SDK 默认 5 分钟 TTL，不支持自定义

## 设计：三层 Session 策略

根据 `time.time() - last_activity` 判断 session 温度，在下一次 query 前执行对应策略：

### 热（Hot）：间隔 < warm_threshold（默认 60 分钟）

- **行为**：直接复用 session，不做任何操作
- **原因**：缓存仍在 TTL 内，命中率高，成本最低
- **实现**：现有逻辑不变

### 温（Warm）：warm_threshold ≤ 间隔 < reset_threshold（默认 1 小时 ~ 4 小时）

- **行为**：query 前先发送 `/compact` 压缩 context
- **原因**：缓存已过期，下次 query 会触发 cache write。先 compact 减小 context 大小，降低 write 成本
- **实现**：通过 SDK `client.query("/compact")` 发送压缩指令，等待完成后再发送用户消息
- **效果**：例如 6MB context compact 到 ~500KB，cache write 成本从 $4 降到 ~$0.3

### 冷（Cold）：间隔 ≥ reset_threshold（默认 4 小时）

- **行为**：`/reset` 关闭旧 session，创建全新 session，注入 always-on 记忆
- **原因**：对话已中断太久，旧 context 中大部分内容已不相关，compact 后仍然浪费。不如重开 session，仅注入关键记忆
- **实现**：
  1. 断开当前连接（触发正常的 disconnect 流程：SDK flush + JSONL 归档 + 记忆提取）
  2. 清除 session_id，创建新 session
  3. 从记忆系统查询 importance >= 8 的 always-on 记忆
  4. 将记忆注入 system_append，确保新 session 知道用户偏好和关键上下文

### Context 长度上限（安全网）

- **触发条件**：不管间隔多久，context tokens 超过 max_context_tokens（默认 200,000）
- **行为**：强制 `/compact`
- **原因**：防止 context 无限增长，避免单次 cache write 天价
- **实现**：从 SDK ResultMessage 的 metadata 中获取 context token 数（需要确认 SDK 是否暴露此信息；如果没有，用 JSONL 文件大小估算：1 byte ≈ 0.3 token）

## 配置项设计

```yaml
session:
  idle_timeout: 600          # 现有：空闲断连（秒）
  max_age: 604800            # 现有：session 最大生命周期（秒）
  warm_threshold_minutes: 60     # 热→温 边界（对齐主 Agent prompt cache TTL 1h）
  reset_threshold_hours: 4       # 温→冷 边界（cache 过期后放宽换任务边界）
  context_soft_threshold: 160000 # 软阈值：提前 compact 避开 180-200k 效率衰减区
  context_hard_threshold: 200000 # 硬阈值：强制 compact，失败则建议 reset
```

## 记忆注入设计

冷启动时，从 SQLite memories 表查询 always-on 记忆：

```python
# retriever.py 已有 _fetch_always_on() 方法：
# SELECT * FROM memories WHERE importance >= 8 ORDER BY importance DESC, created_at DESC
```

格式化为 system_append 的一部分：

```
## 用户记忆（来自历史对话）

- [preference] 用户偏好 A
- [decision] 重要决策 B
- [fact] 关键事实 C
```

**注意**：system_append 拼接顺序为：原有 channel 模板 + 记忆上下文。记忆部分用明确标题分隔，避免与模板指令混淆。

## Token 估算方案

优先方案：从 SDK `ResultMessage` 中读取 token 使用信息（`usage.input_tokens` 等）。
每次 query 完成后更新 `_LiveSession.last_context_tokens` 字段。

备选方案：如果 SDK 不暴露 token 数，用 JSONL 文件大小 * 0.3 估算（保守值）。

## 实现步骤概览

1. **Task 1 - 配置项与 session 温度判断**：新增配置常量，实现 `_get_session_temperature()` 方法
2. **Task 2 - Warm 策略：compact 前置**：在 query 前检测温度，warm 时先执行 /compact
3. **Task 3 - Cold 策略：reset + 记忆注入**：实现 session reset 和 always-on 记忆注入
4. **Task 4 - Context 长度安全网**：token 追踪 + 超限自动 compact
5. **Task 5 - 集成测试 + 配置文档更新**：端到端测试覆盖三层策略

## 任务依赖关系

```
Task 1 (配置 + 温度判断)
  ├── Task 2 (Warm: compact) — 依赖 Task 1
  ├── Task 3 (Cold: reset + 记忆) — 依赖 Task 1
  └── Task 4 (Context 安全网) — 依赖 Task 1
        └── Task 5 (集成测试) — 依赖 Task 2, 3, 4
```

Task 2/3/4 可并行但为降低冲突风险建议串行（都改 cc.py 同一个文件）。
推荐顺序：Task 1 → Task 2 → Task 3 → Task 4 → Task 5。

## 风险与注意事项

1. **SDK /compact 行为**：需确认通过 `client.query("/compact")` 是否能触发 compact。如果 SDK 不支持内置命令，可能需要用 `client.query("请压缩上下文")` 让 CC 自行决定。
2. **记忆注入 token 预算**：always-on 记忆不应超过 2000 tokens，超出时按 importance 截断。retriever.py 已有截断逻辑可复用。
3. **Compact 耗时**：compact 可能需要 10-30 秒。应通知用户正在整理上下文（通过飞书 typing reaction 或消息提示）。
4. **并发安全**：compact/reset 操作期间如有新消息到达，需排队等待。当前 `execute()` 已是 per-channel 串行，无需额外锁。
5. **记忆提取触发**：cold reset 时的 disconnect 会触发正常的记忆提取流程（JSONL 归档 → LLM extractor），确保旧对话的记忆不丢失。
