# outbox.json 格式规范

Brain daemon 通过轮询 `outbox.json` 获取你的执行状态。**格式为 JSON，必须机器可解析。**

## 写入流程（强制）

1. 将结果写入 `outbox.json`
2. 运行 `python validate_outbox.py` 校验
3. 如果校验失败，根据错误信息修正后重新写入，再次校验
4. 校验通过后才能继续下一步工作

## JSON Schema

```json
{
  "status": "TASK_DONE | TASK_BLOCKED | TASK_PROGRESS",
  "summary": "string（必填，描述当前进展或完成内容）",
  "reason": "string（TASK_BLOCKED 时必填）",
  "stage": "string（TASK_PROGRESS 时必填）",
  "artifacts": ["string"],
  "test_instructions": "string（TASK_DONE 时推荐，描述如何测试/验证）"
}
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| status | string | 是 | 只能是 `TASK_DONE`、`TASK_BLOCKED`、`TASK_PROGRESS` |
| summary | string | 是 | 一段话描述当前进展或完成内容 |
| reason | string | TASK_BLOCKED 时必填 | 阻塞原因 |
| stage | string | TASK_PROGRESS 时必填 | 当前阶段描述 |
| artifacts | string[] | 否 | 产出物路径或链接 |
| test_instructions | string | TASK_DONE 时必填 | 告诉用户如何验证改动（测试命令及结果、启动命令、URL、操作步骤等） |

## 示例

### 完成

```json
{
  "status": "TASK_DONE",
  "summary": "实现了用户登录 API，包含 JWT 生成和密码哈希校验，3 个单元测试全部通过",
  "artifacts": ["src/auth/login.py", "tests/test_login.py"],
  "test_instructions": "运行 `python manage.py runserver`，访问 POST /api/auth/login 传入 {username, password}，预期返回 JWT token"
}
```

### 阻塞

```json
{
  "status": "TASK_BLOCKED",
  "reason": "缺少 Redis 连接配置，workspace 中没有 .env 文件",
  "summary": "缓存层代码已完成，但无法测试"
}
```

### 进度

```json
{
  "status": "TASK_PROGRESS",
  "stage": "数据库 schema 已完成",
  "summary": "完成了 3 张表的 migration 文件和 ORM 模型，下一步实现 API 路由"
}
```
