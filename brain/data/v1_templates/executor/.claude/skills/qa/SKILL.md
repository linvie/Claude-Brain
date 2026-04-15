---
name: qa
description: Auto-detect project type and run quality checks (lint + test + build)
allowed-tools: Bash, Read, Glob, Grep
---

# Quality Assurance (Auto-Detect)

检测项目类型并运行对应 QA 工具。

## Step 1: 检测项目类型

按优先级检查项目根目录下的标志文件：

| 文件 | 项目类型 |
|------|---------|
| `pyproject.toml` 或 `requirements.txt` | Python |
| `package.json` | Node.js |
| `go.mod` | Go |
| `Cargo.toml` | Rust |
| `Gemfile` | Ruby |
| 其他 | 按 README 和目录结构判断 |

## Step 2: 运行对应工具

### Python
- Lint: `uv run ruff check .`（如 pyproject.toml 有 `[tool.ruff]`）或 `python -m pyflakes`
- Tests: `uv run pytest` 或 `pytest`
- Type check: `uv run mypy .`（如有 mypy 配置）

### Node.js
- Lint: `npm run lint`（如 package.json 有 lint script）
- Tests: `npm test`
- Build: `npm run build`（验证构建通过）
- Type check: `npx tsc --noEmit`（如是 TS 项目）

### Go
- Lint: `go vet ./...`
- Tests: `go test ./...`
- Build: `go build ./...`

### Rust
- Lint: `cargo clippy --all-targets -- -D warnings`
- Tests: `cargo test`
- Build: `cargo build`

### Ruby
- Lint: `bundle exec rubocop`
- Tests: `bundle exec rspec` 或 `rake test`

## Step 3: 输出格式

```
## 项目类型: <Python|Node|Go|Rust|Ruby|Other>

### Lint: PASS / FAIL
（如 FAIL，列出关键问题，不超过 10 条）

### Tests: PASS / FAIL (N passed, M failed)
（如 FAIL，列出失败的测试名和错误摘要）

### Build: PASS / FAIL
（如适用）

### 综合判断
可以提交 / 需要修复（具体指出哪些问题阻塞）
```

## 调用时机

- 完成一个功能点，提交前
- 写入 TASK_DONE outbox 前
- 被 pre-commit hook 阻塞后想系统性排查
