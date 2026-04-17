# CCBrain 开发状态

最后更新：2026-04-17

---

## 已完成

### v0.2.x — MVP
- asyncio 主循环（Notion 轮询 + 飞书 adapter 并行）
- `~/.ccbrain/` 数据目录分离
- `ccbrain` 全局 CLI（init/config/install/start/stop/restart/status/logs）
- launchd 后台服务（bootout/bootstrap 可靠 stop/start）
- 7 个分类 logger
- 飞书 WebSocket 长连接 + Interactive Card 回复
- Emoji reaction 思考指示器
- Per-channel workspace + 消息队列 + Session resume
- 用户 allowlist
- 命令系统（/help /reset /status /btw）+ 即时响应
- /btw 后台任务（Semaphore 限制并发 3）
- lark-cli 集成（ccbrain config lark-cli）
- CC 通过 lark-cli 主动发送进度消息
- Notion Planner/Executor/Tester 三角色
- 记忆系统 Phase A（SQLite + 关键词检索 + 规则提取）
- 记忆系统 Phase B（FTS5 全文搜索 + Haiku LLM 提取 + Context Bridge 三层检索 + Raw Ledger + Daily Views）

### v0.3.x — 飞书打磨 + 质量规则
- 群聊 @bot 过滤
- 卡片消息优化（标题、长内容分段、HTML 标签适配）
- workspace 模板分区（标记区域更新，用户内容保留）
- 启动时自动更新所有 workspace 模板
- ccbrain config reinit-workspace 手动触发
- Executor/Planner 模板加入 QA 规则、执行策略、提交规范
- validate_outbox.py 增强（强制 test_instructions、拒绝占位符）
- 项目 CLAUDE.md 加入版本号规则、验证要求、上下文恢复

### v0.4.x — Notion-飞书融合 + QA 体系
- 飞书 workspace 配 Notion MCP（自然语言操作 Notion）
- system_append 直接注入 chat_id（修复 CC 找不到 chat_id）
- v1 Executor 注入飞书 notify chat_id（遇阻可通知用户）
- v1 任务完成/阻塞自动飞书通知（Brain 自动检测活跃 channel）
- `ccbrain config notion` 自动配置全局 Notion MCP
- `ccbrain install` 注入 shell PATH 到 launchd plist
- lark-cli 限定为紧急通知（常规进度走卡片流式更新）
- QA: ruff + pytest + diff-cover(100%) + import + 架构 + 版本号检查
- Hooks: PreToolUse 6 步检查链 + PostToolUse 锚点测试
- Skills: /review /qa /test-import
- Subagent: qa-reviewer 只读审查
- README 重写，详情拆分到 brain/docs/
- CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70 环境变量注入 launchd
- 三层错误处理（ProcessTransport 自愈 + receive 超时 + 友好文案）
- /doctor 命令（独立 CC 进程诊断）
- Session 持久化（仅 /reset 归档，平时永远 resume）
- CC 持久会话（ClaudeSDKClient 持久连接 + idle disconnect + resume fallback）
- 流式卡片输出（占位卡片 → 每 2s 更新 → 最终结果）
- SDK 管理命令（/model /usage /status 增强）

### v0.5.x — 执行模板 QA 升级
- executor 模板加 `.claude/hooks/pre-commit-detect.sh`：项目类型自适应（Python 强制 ruff，其他语言软提示）
- executor 模板加 3 个 skill：`/qa`（跨语言质量检查）、`/review`（代码审查）、`/test-run`（快速跑测试）
- executor `CLAUDE.md` 加"项目类型自适应"+"可用 Skills"章节
- planner `CLAUDE.md` 强化任务粒度规则（工具调用 < 30、文件 < 10）+ 验收标准强制格式
- 9 个新测试覆盖模板完整性

### v0.6.x — 飞书卡片升级 + 项目迁移
- 卡片 schema 2.0 + wide_screen_mode（宽屏模式）
- Markdown 标题降级（H1→H4，H2~→H5），引用/表格原生保留
- 表格渲染失败自动降级为列表格式（230099/11310 错误码检测）
- Typing emoji reaction 思考指示器 + 完成后自动移除
- 终态卡片 Footer（耗时 · 模型名 · 费用，notation 小字）
- execute() 返回 metadata 三元组（duration_ms、model、total_cost_usd、num_turns）
- `/migrate` skill：existing 项目源码迁移 + AI 配置合并
- Dispatcher 自动创建迁移任务（project_type=existing 时）
- workspace/manager 支持本地路径 cp -r（除 git clone 外）
- NotionClient.create_task() 编程式任务创建
- get_project_info() 返回 project_type 字段
- 110 个测试

### v0.7.x — v0.12.x — 记忆系统 Phase B
- FTS5 全文搜索（trigram tokenizer 支持中英文）
- Haiku LLM 提取（替代正则，TYPE|IMPORTANCE|CONTENT 结构化输出）
- Context Bridge 三层检索（always-on importance>=8 + FTS5 相关性 + 近 7 天 scope 过滤）+ Ebbinghaus 时间衰减
- Raw Ledger JSONL 归档 + memory_sessions 生命周期追踪
- Daily Views 每日摘要生成（每 6h 检查，Haiku 生成 markdown）
- 历史飞书 session 回填（backfill.py 一次性脚本）
- 25 个集成测试 + 206 测试通过

### v0.12.x — Executor PR 工作流 + 稳定性修复
- Executor 在 feature branch 工作，完成后推送并创建 PR（不再直推 main）
- outbox.json 新增 pr_url 字段，Brain 写入 Notion 并飞书通知
- V1 模板智能注入（CCBRAIN_TEMPLATE_START/END 标记合并，替代 shutil.copy2 盲覆盖）
- v1 模板从 templates/ 移到 brain/data/v1_templates/，确保 wheel 打包
- _reinit_all_workspaces() 检测 inbox.json/outbox.json 跳过 v1 workspace
- Notion API 重试机制（_call_with_retry，自动重试 ConnectionError/SSLError/Timeout）
- outbox 竞态条件修复（TASK_DONE 缺 pr_url 时不处理，等待重试）
- 记忆 bug 修复（_find_sdk_jsonl 路径、extractor 触发顺序、summarized_at 字段拆分）

### v0.13.x — Lark 国际版
- feishu.platform 配置项（feishu/lark），自动切换 API 域名和 WebSocket 域名
- ccbrain init 引导新增平台选择步骤
- 修复 ccbrain init 选择 Lark 后 platform 未写入 config.yaml

### v0.14.x — 初始化引导增强
- ccbrain init 权限和事件订阅引导（分类展示 + 用途说明）
- docs/feishu.md 重构（权限表格、Lark 国际版控制台 URL）
- /brain-init skill 复制到飞书 channel workspace 模板

### v0.15.x — 心跳 + 交互卡片
- Heartbeat 心跳机制（build_heartbeat_prompt + run_heartbeat + 飞书通知）
- 合并 HEARTBEAT_SYSTEM.md（内建）和 HEARTBEAT.md（用户自定义）
- main.py asyncio 定时触发（heartbeat.interval 默认 3600s）
- 飞书交互卡片（card.action.trigger 回调 → 按钮/表单转文本 IncomingMessage）
- /ask skill 模板（CC 自由构造卡片 JSON）
- 263 个测试通过

### v0.16.x — v0.20.x — Session 生命周期优化
基于 Anthropic prompt caching TTL（5 分钟）设计三层 session 策略，自动优化每条消息的 cache write 成本。

- **Session 温度判断** `_get_session_temperature()`：根据 `time.time() - last_activity` 判断 hot/warm/cold
- **Hot 策略**（< 5 分钟）：直接复用 session，prompt cache 仍有效，成本最低
- **Warm 策略**（5 分钟 ~ 2 小时）：query 前自动 `/compact` 压缩 context，降低 cache write 成本
- **Cold 策略**（≥ 2 小时）：reset session + 注入 always-on 记忆（importance >= 8），重新开始
- **Context 安全网**：从 ResultMessage.usage 追踪 input_tokens，超过 200k 时不论温度强制 compact
  - 备选方案：JSONL 文件大小 * 0.3 估算 token 数
- 配置项：`session.warm_threshold_minutes`、`session.reset_threshold_hours`、`session.max_context_tokens`
- compact 失败不阻塞用户 query（降级跳过）
- 31 个集成测试覆盖端到端流程 + 390 测试通过
- 详见 `docs/tech_plan.md`

---

## 待办（按功能域）

### 记忆系统（P1）

当前：Phase B 完成。FTS5 全文搜索 + Haiku LLM 提取 + 三层 Context Bridge + Daily Views。

- [x] **Phase B：LLM 提取 + FTS5 检索 + Daily Views**（Done，v0.12.0）
  - Haiku LLM 提取关键事实（替代正则）
  - FTS5 trigram 全文搜索（中英文）
  - Context Bridge 三层检索 + Ebbinghaus 时间衰减
  - Raw Ledger JSONL 归档 + memory_sessions 生命周期追踪
  - Daily Views 每日摘要生成
  - 核心信息跨 session 保留（通过 scope + importance 分层）
- [ ] **Phase C：MCP tools remember/recall**（P2）— CC 可主动读写记忆
- [ ] **Phase D：dreaming 后台整理**（P2）— 空闲期自动蒸馏

### 定时任务 / 主动感知（P1）

- [x] **Heartbeat 心跳机制**（Done，v0.15.0）
  - main.py asyncio 定时触发，合并 HEARTBEAT_SYSTEM.md + HEARTBEAT.md
  - 隔离 CC session 执行检查，含 NO_ACTION 时静默，否则推送飞书
  - 核心价值：brain 从"等人问"变"主动做事"

### 飞书体验（P1-P3）

参考调研：openclaw-lark 源码分析（2026-04-14）

**Bot Menu 快捷指令**（零代码，后台配置）：
- [ ] 飞书开发者后台配置悬浮菜单：/help /status /reset /model /usage /doctor
- 配置路径：应用详情 → 机器人 → 编辑机器人自定义菜单 → 悬浮菜单 → 响应动作"发送文字消息"
- CCBrain 无需代码改动，菜单触发的是普通文本消息

**v0.7 — CardKit 流式**（P2，需验证 lark-oapi 支持）：
- [ ] CardKit API 流式（cardkit.v1.card.create + cardElement.content，100ms 粒度打字机效果）
- [ ] 三级降级：CardKit → im.message.patch → 静态消息
- [ ] 节流控制：100ms CardKit / 1500ms IM patch + 长间隔批处理
- [ ] Reasoning 折叠面板（`<think>` 提取 → collapsible_panel）

**待定 — deliver 多卡片分发**（需设计框架层调度）：
- [ ] CC 单 turn 多段输出（tool results + 最终答案）分卡片发送

- [x] **交互卡片**（Done，v0.15.0）：card.action.trigger 回调 + /ask skill，详见 `docs/feishu-interactive-cards-plan.md`
- [ ] **图片消息**（P3）：adapter 接收 image → 下载到 workspace → prompt 引导 CC 用 Read 读图

### Prompt / 人格（P2-P3）

- [ ] **Prompt 模块化 full/minimal**（P2）：主会话 full，`/btw` 后台任务 minimal（不加载飞书规则/流式），省 token
- [ ] **SOUL.md 人格层**（P3）：独立的 AI 性格和说话风格文件

### 可靠性 / 监测

- [ ] **context 压缩监测**（P2）：验证 CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70 是否被 SDK 子进程继承
- [ ] **Health monitor**（P3）：服务存活检测 + 自动重启

### 安全

- [ ] **workspace 权限沙箱**（P2）：deny 敏感路径（~/.ssh、config.yaml）
- [ ] **workspace git 备份**（P3，暂不做）：收益低于复杂度

### 扩展性

- [ ] **多 channel adapter**（P3）：Telegram 等，参考 Claude Code Channels MCP 模式
- [ ] **独立 QA Agent**（P3）：Notion 任务流新 task_type
- [ ] **SDK 功能扩展**（P3）：/effort /stop /context、MCP 状态、预算限制

### TS 迁移（长期方向，非紧急）

调研结论（2026-04-14）：放弃 OpenClaw 替代 runtime 定位，CCBrain 保持独立项目。
TS 迁移仍有价值（SDK 功能超集 + TS 生态更适合未来 channel 扩展），但不以兼容 OpenClaw 为动机。
详见 Notion：CCBrain v2 TypeScript + OpenClaw 调研页。

---

## 已知问题
- lark-oapi ws.Client event loop patch 可能随 SDK 升级失效
- ~~记忆提取（extractor.py）简单正则效果有限~~ — Phase B 已用 Haiku LLM 替代
- CC bypassPermissions 安全依赖 allowlist

---

## 文档索引

迁移到 Notion 时按重要性排序：

### 核心文档（必须迁移）
- **开发状态**（本文件）：当前版本、已完成功能、待办事项、已知问题
- **技术架构** `brain/docs/architecture.md`：源码结构、分层依赖、v2 消息流、日志体系
- **飞书设置** `brain/docs/feishu.md`：应用创建、命令列表、消息处理、安全配置
- **Notion 设置** `brain/docs/notion.md`：数据库字段、使用流程（新建/导入/规划/测试）、调度规则、QA 体系

### 设计文档（按需迁移）
- **QA 系统** `docs/qa-system.md`：ruff + pytest + diff-cover + hooks + skills 的完整 QA 体系设计
- **/doctor 命令设计** `docs/doctor-design.md`：独立诊断命令的设计思路
- **飞书交互卡片方案** `docs/feishu-interactive-cards-plan.md`：callback-as-message 模式设计
- **模板优化报告** `docs/template-optimization-report.md`：pilot 插件分析 + 模板升级方案

### 历史文档（仅参考）
- **技术设计文档 v1** `docs/Claude Brain — 技术设计文档.md`：v1 初始架构设计
- **技术架构文档 v2** `docs/Claude Brain v2 — 技术架构文档.md`：v2 飞书对话流设计

### 调研文档（已在 Notion）
- OpenClaw 记忆架构分析：Notion Knowledge Base
- CCBrain v2 TypeScript 迁移调研：Notion Knowledge Base
