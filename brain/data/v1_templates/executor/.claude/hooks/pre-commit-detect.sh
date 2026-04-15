#!/bin/bash
# PreToolUse hook: 检测项目类型，调用对应 lint 工具
# 策略: Python 项目有 ruff 配置则强制，其他语言仅软提示（不阻塞）

cd "$CLAUDE_PROJECT_DIR" || exit 0

STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
[ -z "$STAGED" ] && exit 0

# Python: 有 ruff 配置则强制通过
if [ -f "pyproject.toml" ]; then
    PY_FILES=$(echo "$STAGED" | grep '\.py$')
    if [ -n "$PY_FILES" ] && grep -q "\[tool\.ruff" pyproject.toml 2>/dev/null; then
        if command -v uv >/dev/null 2>&1; then
            OUTPUT=$(uv run ruff check $PY_FILES 2>&1)
        elif command -v ruff >/dev/null 2>&1; then
            OUTPUT=$(ruff check $PY_FILES 2>&1)
        else
            exit 0
        fi
        if [ $? -ne 0 ]; then
            echo "=== Ruff Lint FAILED ===" >&2
            echo "$OUTPUT" >&2
            echo "修复 lint 错误后再提交（auto-fix: ruff check --fix）" >&2
            exit 2
        fi
    fi
    exit 0
fi

# Node.js: 软提示
if [ -f "package.json" ]; then
    if command -v npm >/dev/null 2>&1 && grep -q '"lint"' package.json 2>/dev/null; then
        npm run lint --silent 2>&1 || echo "⚠ lint 有问题（非阻塞）" >&2
    fi
    exit 0
fi

# Go: 软提示
if [ -f "go.mod" ]; then
    if command -v go >/dev/null 2>&1; then
        go vet ./... 2>&1 || echo "⚠ go vet 有警告（非阻塞）" >&2
    fi
    exit 0
fi

# Rust: 软提示
if [ -f "Cargo.toml" ]; then
    if command -v cargo >/dev/null 2>&1; then
        cargo check --quiet 2>&1 || echo "⚠ cargo check 有警告（非阻塞）" >&2
    fi
    exit 0
fi

exit 0
