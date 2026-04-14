---
name: test-run
description: Run project tests with auto-detection
allowed-tools: Bash
argument-hint: "[test path 或 pattern，留空跑全量]"
---

# Run Tests

自动检测项目类型并运行测试。

## Step 1: 检测项目类型

按优先级：
- `pyproject.toml` → Python
- `package.json` → Node.js
- `go.mod` → Go
- `Cargo.toml` → Rust
- `Gemfile` → Ruby

## Step 2: 运行测试

### Python
- `uv run pytest $ARGUMENTS` （优先）
- `pytest $ARGUMENTS`
- `python -m unittest $ARGUMENTS`

### Node.js
- `npm test -- $ARGUMENTS`
- `npx jest $ARGUMENTS`（如是 jest 项目）
- `npx vitest run $ARGUMENTS`（如是 vitest 项目）

### Go
- `go test ./... $ARGUMENTS`

### Rust
- `cargo test $ARGUMENTS`

### Ruby
- `bundle exec rspec $ARGUMENTS`
- `bundle exec rake test $ARGUMENTS`

## Step 3: 报告

```
## 测试结果

- 通过: N
- 失败: N
- 跳过: N
- 耗时: Xs

### 失败详情
（如果有失败，列出失败测试名 + 错误摘要）

### 综合判断
全部通过 / 有 N 个失败需要修复
```

## 调用时机

- 完成一个功能点，快速验证无回归
- 修复 bug 后确认修复有效
- 不需要完整 QA（lint + test + build）时的轻量检查
