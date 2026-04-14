# Memory System Phase B 架构设计

> 状态：**已实施完成**（v0.8.0–v0.12.0） | 设计日期：2026-04-14 | 实施日期：2026-04-14

## 1. 概述

Phase A（当前）：SQLite `memories` 表 + 正则 extractor + 关键词 LIKE 检索。只写入不整理，正则提取效果有限，无蒸馏/合并机制。

Phase B（本次）：完整记忆生命周期 — Raw Ledger 归档 → LLM 提取 → FTS5 检索 → 每日摘要视图。

### 设计原则

1. **SQLite-first**：不引入向量 DB，FTS5 足够（参考 Google Always-On 方案）
2. **Haiku 驱动**：提取和摘要用 Haiku（便宜、快速），不消耗 Opus/Sonnet 配额
3. **文件 + DB 双存储**：JSONL 保留原始对话（可追溯），SQLite 存结构化记忆（可查询）
4. **渐进式替换**：Phase A 的 `memories` 表保留，新增 `memory_sessions` 表和 FTS5 索引
5. **v1/v2 边界不破坏**：记忆模块只在 `brain/memory/` 和 `brain/main.py` 改动

## 2. 文件布局

```
~/.ccbrain/memory/              ← 新目录（DATA_DIR / "memory"）
  ledger/
    {session_id}.jsonl          ← Raw Ledger：SDK 对话原始记录
  views/
    {YYYY-MM-DD}.md             ← Daily View：Haiku 每日摘要

brain/memory/
  __init__.py
  store.py                      ← 修改：新增 FTS5 表 + scope 字段 + 迁移
  retriever.py                  ← 重写：FTS5 检索 + 时间衰减 + 分层注入
  extractor.py                  ← 重写：LLM 提取替代正则
  ledger.py                     ← 新增：JSONL 归档管理
  views.py                      ← 新增：每日摘要生成
```

## 3. SQLite Schema 变更

### 3.1 新增 `memory_sessions` 表

```sql
CREATE TABLE memory_sessions (
    session_id    TEXT PRIMARY KEY,
    channel_id    TEXT NOT NULL,
    opened_at     INTEGER NOT NULL,       -- Unix timestamp
    closed_at     INTEGER,                -- NULL = 仍活跃
    jsonl_path    TEXT,                    -- 关闭后写入 ledger 路径
    summarized_at INTEGER,                -- Haiku 处理后写入
    message_count INTEGER DEFAULT 0       -- 该 session 的消息数（用于判断是否值得提取）
);
```

### 3.2 `memories` 表迁移

```sql
-- 新增字段
ALTER TABLE memories ADD COLUMN scope TEXT DEFAULT 'global';
-- scope 取值：global（全局）、channel:{id}（频道级）

-- FTS5 虚拟表（全文搜索）
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    tags,
    content=memories,
    content_rowid=id
);

-- 触发器：memories 写入时同步 FTS5
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;

CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
END;

CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags)
    VALUES ('delete', old.id, old.content, old.tags);
    INSERT INTO memories_fts(rowid, content, tags)
    VALUES (new.id, new.content, new.tags);
END;
```

## 4. 组件详设

### 4.1 Raw Ledger（`brain/memory/ledger.py`）

**职责**：session 关闭时，复制 Claude SDK 的 JSONL 对话记录到 `~/.ccbrain/memory/ledger/`。

```python
# 核心接口
def get_ledger_dir() -> Path:
    """返回 ledger 目录路径，不存在则创建"""

def archive_session_jsonl(session_id: str, sdk_jsonl_path: Path) -> Path | None:
    """复制 JSONL 到 ledger 目录，返回归档路径。源文件不存在返回 None"""

def get_session_jsonl(session_id: str) -> Path | None:
    """根据 session_id 获取已归档的 JSONL 路径"""
```

**集成点**（`executor/cc.py`）：
- `_LiveSession._ensure_connected()` 成功后：`INSERT INTO memory_sessions (session_id, channel_id, opened_at)`
- `_LiveSession._disconnect()` 时：
  1. 查找 SDK JSONL 路径（`~/.claude/projects/.../sessions/{session_id}.jsonl`）
  2. 调用 `archive_session_jsonl()` 复制
  3. `UPDATE memory_sessions SET closed_at, jsonl_path WHERE session_id = ?`

**SDK JSONL 路径定位**：
- Claude SDK 存储路径：`~/.claude/projects/{project_hash}/sessions/{session_id}.jsonl`
- 需要从 `_LiveSession.cwd` 推导 project_hash，或在 connect 时从 SDK 获取

### 4.2 LLM Extractor（`brain/memory/extractor.py` 重写）

**职责**：session 关闭后，用 Haiku 从 JSONL 提取结构化记忆。

```python
# 核心接口
async def extract_from_session(
    conn: sqlite3.Connection,
    session_id: str,
    jsonl_path: Path,
    channel_id: str,
) -> int:
    """从 JSONL 提取记忆，返回提取数量"""
```

**提取流程**：
1. 读取 JSONL，提取用户消息和助手回复的文本部分
2. 如果对话过短（< 3 轮），跳过
3. 拼接对话摘要（截断到 ~4000 tokens，保留首尾）
4. 调用 Haiku（通过 `executor/cc.one_shot_query` 或直接 Anthropic API）：

```
从以下对话中提取值得记住的信息。每条记忆一行，格式：
TYPE|IMPORTANCE|CONTENT
TYPE: fact/preference/decision/context
IMPORTANCE: 1-10
CONTENT: 简洁描述

只提取长期有价值的信息，忽略临时性操作细节。
```

5. 解析 Haiku 输出，每行写入 `memories` 表
6. `UPDATE memory_sessions SET summarized_at = ? WHERE session_id = ?`

**与 Phase A 兼容**：
- 保留 `extract_and_store()` 函数签名用于过渡期
- 新增 `extract_from_session()` 作为主提取入口
- Phase A 的 per-message 正则提取可并行保留，逐步淘汰

**Haiku 调用方式**：
- 优先：直接用 `anthropic` SDK 调 `claude-haiku-4-5-20251001`（最便宜）
- 备选：用 `executor.cc.one_shot_query()` 包装（复用现有基础设施，但启动 CC 进程开销大）
- **推荐直接 API**：新增 `brain/memory/_llm.py` 封装 Anthropic API 调用，供 extractor 和 views 共用

### 4.3 Context Bridge（`brain/memory/retriever.py` 重写）

**职责**：session 开始时，检索相关记忆注入 system prompt。

```python
# 核心接口
def build_memory_context(
    conn: sqlite3.Connection,
    message: str,
    channel_id: str | None = None,
    max_tokens: int = 2000,
) -> str:
    """构建记忆上下文，返回格式化文本"""
```

**检索策略（三层）**：

1. **Always-on 层**（高重要性）：
   ```sql
   SELECT * FROM memories
   WHERE importance >= 8
   ORDER BY importance DESC, created_at DESC
   LIMIT 5
   ```

2. **Relevance 层**（FTS5 全文搜索）：
   ```sql
   SELECT m.* FROM memories m
   JOIN memories_fts ON memories_fts.rowid = m.id
   WHERE memories_fts MATCH ?
   ORDER BY rank
   LIMIT 10
   ```

3. **Recent 层**（最近记忆）：
   ```sql
   SELECT * FROM memories
   WHERE created_at > ? -- 最近 7 天
   AND (scope = 'global' OR scope = ?)  -- channel 过滤
   ORDER BY created_at DESC
   LIMIT 5
   ```

**时间衰减评分**：
```python
def decay_score(importance: int, age_days: float) -> float:
    """Ebbinghaus-inspired decay: importance * e^(-age/half_life)"""
    half_life = 30  # 30 天半衰期
    return importance * math.exp(-0.693 * age_days / half_life)
```

**输出格式**：
```
以下是你已知的相关信息：

## 重要信息
- [fact] 用户偏好 Python 开发 (重要度: 9)
- [preference] 代码风格偏好简洁 (重要度: 8)

## 最近对话
- [context] 昨天讨论了 API 重构方案
- [decision] 选择了 SQLite 而非 PostgreSQL
```

### 4.4 Daily Views（`brain/memory/views.py`）

**职责**：每日生成摘要，合并碎片记忆。

```python
# 核心接口
async def generate_daily_view(conn: sqlite3.Connection, date: str | None = None) -> Path | None:
    """生成指定日期的摘要视图，返回文件路径。无新内容返回 None"""

async def run_daily_views_job(conn: sqlite3.Connection):
    """扫描未摘要的 session，生成视图。由 main.py 定时调用"""
```

**生成流程**：
1. 查询当日已关闭但未摘要的 session：
   ```sql
   SELECT session_id, jsonl_path, channel_id FROM memory_sessions
   WHERE summarized_at IS NULL
     AND closed_at IS NOT NULL
     AND date(closed_at, 'unixepoch') = date(?, 'unixepoch')
   ```
2. 读取各 session 的 JSONL（或已提取的 memories）
3. 调用 Haiku 生成结构化摘要
4. 写入 `~/.ccbrain/memory/views/{YYYY-MM-DD}.md`
5. 更新 `memory_sessions.summarized_at`

**摘要模板**：
```markdown
# Daily Memory View — 2026-04-14

## Sessions
- [channel_id] 14:30-15:45: 讨论了记忆系统设计，决定用 FTS5 替代向量搜索
- [channel_id] 16:00-16:30: 修复了飞书消息卡片样式问题

## Key Facts Learned
- 用户偏好渐进式架构升级
- CCBrain 项目使用 SQLite 作为唯一存储

## Decisions Made
- Phase B 不引入向量 DB
- Haiku 负责提取和摘要

## Open Questions
- SDK JSONL 路径如何从 cwd 推导？
```

**调度策略**：
- 在 `main.py` 的 asyncio 主循环中，每 6 小时检查一次（或每次 session disconnect 后触发）
- 不阻塞消息处理（用 `asyncio.create_task` 后台运行）

### 4.5 Haiku LLM 封装（`brain/memory/_llm.py`）

**职责**：封装 Anthropic API 调用，供 extractor 和 views 共用。

```python
# 核心接口
async def haiku_complete(
    system: str,
    user_message: str,
    max_tokens: int = 1024,
) -> str:
    """调用 Haiku，返回文本结果。失败返回空字符串"""
```

**实现要点**：
- 使用 `anthropic.AsyncAnthropic` 客户端
- model: `claude-haiku-4-5-20251001`
- 自动重试（429/500，最多 2 次）
- API key 从环境变量 `ANTHROPIC_API_KEY` 获取（已被 CC 进程使用）
- 日志记录调用耗时和 token 使用

## 5. 集成点一览

```
Session lifecycle:
  connect()    → INSERT memory_sessions (opened)
  query()      → message_count++
  disconnect() → copy JSONL → UPDATE memory_sessions (closed)
                → async: extract_from_session()
                → async: generate_daily_view() (if due)

Message handling:
  new message  → build_memory_context() → inject into system_append
  CC response  → (Phase A extract_and_store 可选保留)
```

## 6. 配置项（config.yaml 新增）

```yaml
memory:
  enabled: true
  ledger_dir: "~/.ccbrain/memory/ledger"
  views_dir: "~/.ccbrain/memory/views"
  extraction_model: "claude-haiku-4-5-20251001"
  views_interval_hours: 6          # 每日摘要检查间隔
  max_context_tokens: 2000         # 注入记忆的 token 上限
  decay_half_life_days: 30         # 时间衰减半衰期
  always_on_threshold: 8           # importance >= 此值的记忆始终注入
```

## 7. 依赖变更

- 新增：`anthropic`（Haiku API 直接调用）— 检查是否已在 dependencies 中
- 无新外部依赖（FTS5 是 SQLite 内置）

## 8. 不在 Phase B 范围

- MCP tools（remember/recall）→ Phase C
- 后台 Dreaming 自动蒸馏 → Phase D
- 向量搜索 / embeddings → 暂缓，FTS5 足够
- Bi-temporal（valid_time / transaction_time）→ 暂缓
- 跨 channel 记忆合并策略 → Phase C

## 9. 子任务拆解

### Task 1: Schema + Raw Ledger 基础设施
- 新增 `memory_sessions` 表（db.py 迁移）
- 新增 `brain/memory/ledger.py`
- 修改 `executor/cc.py` _LiveSession: connect 时 INSERT, disconnect 时 COPY+UPDATE
- 新增 `memory` 配置段到 config.yaml
- import 验证 + 单元测试

### Task 2: Haiku LLM 封装 + LLM Extractor
- 新增 `brain/memory/_llm.py` (Haiku API 封装)
- 重写 `brain/memory/extractor.py`（LLM 提取替代正则）
- 在 disconnect 流程中触发异步提取
- import 验证 + 测试

### Task 3: FTS5 搜索 + Context Bridge
- `memories` 表迁移：新增 scope 字段 + FTS5 虚拟表
- 重写 `brain/memory/retriever.py`（三层检索 + 时间衰减）
- 修改 `store.py` 的 `add_memory` 触发 FTS5 同步
- import 验证 + 测试

### Task 4: Daily Views 生成器
- 新增 `brain/memory/views.py`
- 在 `main.py` 中添加定时任务
- 生成 markdown 摘要到 views 目录
- import 验证 + 端到端验证

### Task 5: 集成测试 + 文档更新
- 端到端测试：session open → chat → disconnect → extract → view
- 更新 CLAUDE.md 记忆系统章节
- 更新 docs/dev-status.md Phase B 状态
- 版本号更新
