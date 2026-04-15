---
name: review
description: Review staged changes or commit range for correctness, style, completeness
allowed-tools: Bash, Read, Glob, Grep
argument-hint: "[commit range 如 HEAD~3..HEAD，或留空 review staged changes]"
---

# Code Review

审查代码改动质量，给出分级建议。

## 审查范围

- 参数为空：`git diff --cached`（staged changes）
- 参数为 commit range（如 `HEAD~3..HEAD`）：review 该范围
- 参数为文件路径：review 该文件最近改动

## 审查维度

### 1. 正确性
- 逻辑错误、边界条件、未处理的异常
- 异步代码：缺失的 await、资源未关闭
- 并发：race condition、共享状态未加锁
- 错误处理：异常吞没（except: pass）、重要错误未日志化

### 2. 完整性
- 新增函数/模块是否有对应测试
- 新配置项是否文档化
- 新依赖是否加入 package manifest（requirements.txt / package.json / go.mod / Cargo.toml）
- 新功能是否更新 README 或相关文档

### 3. 风格
- Commit message 是否符合 Conventional Commits（type(scope): description）
- 无调试代码残留（print、console.log、debugger、pdb.set_trace）
- 无硬编码密钥、API token、本地路径
- 命名一致性（camelCase / snake_case / PascalCase）

### 4. 可维护性
- 复杂函数是否需要拆分（>50 行、嵌套层次 >4）
- 魔法数字/字符串是否提取为常量
- 重复代码是否抽取共用函数

## 输出格式

按文件分组，每个问题标明严重性：

```
### path/to/file.ext:line_number
- [CRITICAL] 严重问题，必须修复（如 race condition、硬编码密钥）
- [WARNING] 应该修复（如调试代码、缺失测试）
- [SUGGESTION] 可改进（如命名、抽函数）
```

末尾综合判断：

```
## 综合建议

**APPROVE** / **REQUEST_CHANGES** / **COMMENT**

- CRITICAL: N 个
- WARNING: N 个
- SUGGESTION: N 个

是否建议合并/提交：是 / 需先修复 critical
```

## 调用时机

- 完成一组改动，写 TASK_DONE 前
- 多个 commit 堆积，想整体审视
- 被 reviewer 打回前自查
