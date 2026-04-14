# Notion 设置

## 配置

```bash
ccbrain config notion     # 引导填入 Token 和数据库 ID，自动配置 Notion MCP
```

Token 获取：https://www.notion.so/profile/integrations → 创建 Integration → 复制 Token

数据库 ID 可以手动填入，也可以留空后在 Claude Code 中运行 `/brain-init` 自动创建。

## 数据库字段

### Project 数据库

| 属性名 | 类型 | 说明 |
|--------|------|------|
| project_name | Title | 项目名称 |
| project_type | Select | `new` / `existing` |
| repo_url | URL | GitHub 仓库地址 |
| status | Select | `Active` / `Paused` / `Archived` |
| description | Text | 项目背景描述 |

### Task 数据库

| 属性名 | 类型 | 说明 |
|--------|------|------|
| task_name | Title | 任务名称 |
| description | Text | 任务描述 |
| task_type | Select | `planner` / `executor` / `tester` |
| project | Relation | 关联到 Project |
| blocked_by | Relation (self) | 依赖的前置任务 |
| status | Select | `Pending` / `Ready` / `Running` / `Done` / `Blocked` / `Timeout` |
| priority | Select | `High` / `Normal` / `Low` |
| execution_log | Text | 系统自动写入 |

## 使用流程

### 新建项目

1. 在 Project 数据库创建项目（`project_type = new`，`status = Active`）
2. 创建 Task：`task_type = executor`，写 description，`status = Ready`
3. Brain 自动拾取并在空 workspace 中执行

### 导入已有项目（v0.6+）

1. 在 Project 数据库创建项目（`project_type = existing`，`status = Active`，填 `repo_url`）
   - `repo_url` 支持 GitHub URL（`https://github.com/...`）或本地路径（`/Users/xxx/code/...`）
2. Brain 首次分发时**自动创建迁移任务**（`项目迁移：<项目名>`，status=Ready）
3. Executor 使用 `/migrate` skill 完成源码复制 + AI 配置合并
4. 迁移任务完成后，后续 Task 在已有代码基础上工作

迁移任务自动处理：
- GitHub URL → `git clone`，本地路径 → `cp -r`
- `CLAUDE.md`：项目原有内容保留，Brain 规则以 `CCBRAIN:BEGIN/END` 标记追加
- `.claude/settings.json`：permissions 取并集，hooks 追加合并
- `.claude/skills/` 和 `hooks/`：并存不覆盖

### 直接执行（任务明确）

1. 在 Project 数据库创建项目（`status = Active`，填 `repo_url`）
2. 创建 Task：`task_type = executor`，写 description，`status = Ready`
3. Brain 自动拾取并执行

### 先规划再执行（复杂需求）

1. 创建 Task：`task_type = planner`，描述需求
2. Planner CC 自动拆解为子任务，写入技术方案
3. 审阅后将子任务改为 `Ready`

### 测试环境

1. 创建 Task：`task_type = tester`，`status = Ready`
2. CC 生成启动/停止脚本
3. 改为 `Done` 停止，改为 `Ready` 重启

## 调度规则

- 跨 project 并行，同 project 串行
- `blocked_by` 中的任务全部 Done 后才拾取
- 最大并发数：`scheduler.max_concurrent`（默认 3）
- 单任务超时：`task.max_duration`（默认 2 小时）
- 执行日志自动写入 Task 的 `execution_log`

## Executor 自带 QA（v0.5+）

每个 executor workspace 自动获得：

**Pre-commit hook**（自动）：
- Python 项目：ruff 检查（pyproject.toml 有 `[tool.ruff]` 时强制）
- Node/Go/Rust：lint 软提示（不阻塞）

**可用 Skills**：
- `/qa` — 跨语言自动化质量检查（lint + test + build）
- `/review` — 审查 staged changes，按 CRITICAL/WARNING/SUGGESTION 分级
- `/test-run` — 快速跑项目测试

CC 在执行 Notion 任务时会主动调用这些 skill，提升产出质量。无需配置，模板自带。

## 任务生命周期

```
Pending → Ready → Running → Done / Blocked / Timeout
```

- **Pending**：等待前置任务完成
- **Ready**：Brain 下次轮询时拾取
- **Running**：CC 正在执行
- **Done**：完成，摘要和测试方法写回 Notion
- **Blocked**：CC 遇到障碍，原因写入 execution_log
- **Timeout**：超过最大运行时长

## 飞书通知（v0.4+）

任务完成或阻塞时自动发送飞书通知。Brain 自动使用最近活跃的飞书对话作为通知目标，无需手动配置。

如需固定通知到特定群：

```yaml
# ~/.ccbrain/config.yaml
feishu:
  notify_chat_id: "oc_xxx"
```

## 手动创建数据库

如不使用 `/brain-init`：

1. 在 Notion 创建页面，创建上述两个数据库
2. Share 给你的 Integration
3. 从数据库 URL 获取 ID，填入 config

```bash
ccbrain config notion     # 填入 Token 和数据库 ID
```
