# outbox.md 格式规范

Brain daemon 通过轮询 outbox.md 来获取你的执行状态。格式必须严格遵守，否则会被判定为格式异常。

## 格式

```markdown
# Status
<STATUS_TOKEN>

# Summary
<一段话描述当前进展或完成内容>

# Artifacts
<可选：产出物路径、PR 链接等>
```

## Status Token

| Token | 含义 | 何时使用 |
|---|---|---|
| `TASK_DONE` | 任务完成 | 所有工作已完成，代码已提交 |
| `TASK_BLOCKED:原因` | 遇到阻塞 | 无法继续，需要人工介入（缺少权限、依赖缺失、需求不明确等） |
| `TASK_PROGRESS:阶段描述` | 中途进度 | 长任务中完成了一个主要阶段，报告进度 |

## 规则

1. `# Status` 下一行必须是 status token，不得有空行或前置文字
2. `# Summary` 必须存在，内容不得为空
3. `# Artifacts` 为可选段落
4. 不得在格式之外添加任何前置说明或额外段落
5. 每次写入 outbox.md 时完整覆盖，不要追加

## 示例

### 完成

```markdown
# Status
TASK_DONE

# Summary
实现了用户登录 API，包含 JWT 生成和密码哈希校验。添加了 3 个单元测试，全部通过。

# Artifacts
- src/auth/login.py
- tests/test_login.py
- PR: https://github.com/user/repo/pull/42
```

### 阻塞

```markdown
# Status
TASK_BLOCKED:需要 Redis 连接配置，当前 workspace 中没有 .env 文件

# Summary
已完成缓存层代码编写，但无法测试因为缺少 Redis 连接信息。
```

### 进度

```markdown
# Status
TASK_PROGRESS:数据库 schema 已完成

# Summary
完成了 3 张表的 migration 文件和 ORM 模型定义，下一步实现 API 路由。
```
