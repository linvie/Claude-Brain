---
name: migrate
description: Migrate existing project into Brain workspace — clone/copy repo, merge AI configs
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Project Migration

将 existing 项目迁移到 Brain workspace，处理 AI 配置合并。

## 前置条件

读取 `inbox.json`，确认：
- `context.repo_url` 存在（GitHub URL 或本地路径）
- 这是一个迁移任务（task_name 含"迁移"或 description 指明迁移）

## Step 1: 获取源码

根据 `repo_url` 格式：
- `https://` 或 `git@` → `git clone <url> _source`
- 本地路径（`/` 或 `~/` 开头）→ `cp -r <path>/. _source`（注意 `/. ` 复制隐藏文件）

验证 `_source/` 目录存在且非空。

## Step 2: 评估现有 AI 配置

检查 `_source/` 中的 AI 配置文件，逐项记录：

```
## AI 配置评估

### CLAUDE.md
- [ ] 存在 / 不存在
- 内容摘要：（简述项目规范要点）

### .claude/settings.json
- [ ] 存在 / 不存在
- permissions.allow: [列出]
- permissions.deny: [列出]
- hooks: [列出已注册的 hooks]

### .claude/hooks/
- [ ] 存在 / 不存在
- 文件列表：[列出]

### .claude/skills/
- [ ] 存在 / 不存在
- 已有 skills：[列出]

### .claude/commands/
- [ ] 存在 / 不存在

### 其他配置
- .cursorrules / .cursor/ — Cursor 配置
- .github/copilot-instructions.md — Copilot 配置
- .windsurfrules — Windsurf 配置
```

## Step 3: 合并策略

### 3a. 非 AI 配置文件 — 全量复制

```bash
# 复制所有文件，排除 .claude/ 和 CLAUDE.md（后续单独处理）
rsync -a --exclude='.claude/' --exclude='CLAUDE.md' _source/ ./
```

或用 `cp` + 手动排除。**不要覆盖 Brain 注入的文件**（inbox.json、outbox.json、brain_config.json、WORKFLOW.md、OUTBOX_FORMAT.md、validate_outbox.py）。

### 3b. CLAUDE.md — 追加合并

如果源项目有 CLAUDE.md：
1. 读取源项目 CLAUDE.md 内容
2. 读取当前 workspace CLAUDE.md（Brain executor 模板）
3. 将源项目内容放在开头，Brain 模板内容用标记包裹追加：

```markdown
<!-- 以下为源项目原有内容 -->
{源项目 CLAUDE.md 内容}

<!-- CCBRAIN:BEGIN — Brain executor 规则，由系统维护，勿手动修改 -->
{当前 workspace CLAUDE.md 的 executor 规则}
<!-- CCBRAIN:END -->
```

如果源项目没有 CLAUDE.md，保持 Brain 模板不变。

### 3c. settings.json — 深度合并

如果源项目有 `.claude/settings.json`：

```
合并规则：
- permissions.allow: 取并集（Brain 的 + 项目的）
- permissions.deny: 取并集（确保 mcp__notion__* 在 deny 中）
- hooks: 合并数组（Brain 的 hook 追加，不删除项目已有的）
- 其他字段: 项目的优先（Brain 的作为默认值）
```

如果源项目没有，保持 Brain 模板不变。

### 3d. hooks / skills / commands — 并存

- 源项目的 hooks → 复制到 `.claude/hooks/`（不覆盖同名文件）
- 源项目的 skills → 复制到 `.claude/skills/`（不覆盖 qa/review/test-run）
- 源项目的 commands → 复制到 `.claude/commands/`

## Step 4: 验证

1. 检测项目类型（pyproject.toml / package.json / go.mod 等）
2. 安装依赖（如适用）
3. 运行基础验证：
   - Python: `uv run ruff check .` 或 `python -c "import main_module"`
   - Node: `npm install && npm run build`（如有 build script）
   - Go: `go build ./...`
   - Rust: `cargo check`
4. 运行测试（如有）
5. 确认 `.claude/settings.json` 合法（JSON 可解析）

## Step 5: 清理 + 提交

1. 删除 `_source/` 临时目录
2. `git add -A && git commit -m "feat: migrate existing project into workspace"`
3. 写 outbox.json:

```json
{
  "status": "TASK_DONE",
  "summary": "迁移完成：<项目类型>项目，<N>个文件，AI配置<有冲突已合并/无冲突>",
  "artifacts": ["列出关键文件"],
  "test_instructions": "验证命令及结果"
}
```

## 注意事项

- **不要修改源项目的业务代码** — 迁移任务只做复制和配置合并
- **冲突优先保留项目配置** — Brain 规则是补充，不是替代
- 如果 repo_url 无法访问（网络问题/路径不存在），写 TASK_BLOCKED
