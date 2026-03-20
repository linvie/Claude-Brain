---
name: brain-init
description: 一键初始化 Claude Brain 的 Notion 环境（创建数据库、写入 config）
allowed-tools: mcp__notion__API-post-search, mcp__notion__API-get-self, mcp__notion__API-retrieve-a-database, Bash, Read, Edit
---

# Claude Brain Notion 初始化

自动在 Notion 中创建 Claude Brain 所需的完整数据库结构，并将数据库 ID 写回 `config.yaml`。

## 前置检查

1. 调用 `mcp__notion__API-get-self` 验证 Notion Token 是否有效。如果失败，提示用户：
   - 检查 Notion MCP 是否已配置：`claude mcp list`
   - Token 是否正确
   - 停止执行

## 步骤 1：选择位置

2. 调用 `mcp__notion__API-post-search` 搜索 workspace 中可访问的页面。
3. 列出搜索到的页面，让用户选择在哪个页面下创建数据库。如果已存在名为 `Claude Brain` 或 `Claude-Brain` 的页面，优先使用该页面（跳过步骤 2 的创建）。

## 步骤 2：创建 Brain 主页面（如不存在）

4. 使用 Notion REST API（通过 curl）在选定位置下创建页面：

```bash
curl -s -X POST 'https://api.notion.com/v1/pages' \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Notion-Version: 2022-06-28' \
  -H 'Content-Type: application/json' \
  -d '{"parent": {"page_id": "<PARENT_ID>"}, "properties": {"title": {"title": [{"text": {"content": "Claude Brain"}}]}}}'
```

> **重要**：Notion Token 从 `~/.claude.json` 中读取（在 `mcpServers.notion.env.NOTION_TOKEN` 路径下），不要硬编码。

## 步骤 3：创建 Project 数据库

5. 使用 Notion REST API **v2022-06-28** 创建数据库（`mcp__notion__API-create-a-data-source` 在 API v2025-09-03 下不支持创建数据库，必须用 REST API 回退）：

```bash
curl -s -X POST 'https://api.notion.com/v1/databases' \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Notion-Version: 2022-06-28' \
  -H 'Content-Type: application/json' \
  -d '<JSON_BODY>'
```

**Project 数据库属性**：

| 属性名 | 类型 | 说明 |
|---|---|---|
| project_name | title | 项目名称 |
| project_type | select | 选项：`new` (blue), `existing` (green) |
| repo_url | url | GitHub 仓库地址 |
| status | select | 选项：`Active` (green), `Paused` (yellow), `Archived` (default) |
| description | rich_text | 项目背景描述 |

## 步骤 4：创建 Task 数据库

6. 同样使用 REST API v2022-06-28 创建，属性如下：

| 属性名 | 类型 | 说明 |
|---|---|---|
| task_name | title | 任务名称 |
| description | rich_text | 任务描述 |
| task_type | select | 选项：`planner` (purple), `executor` (blue) |
| project | relation | 关联到 Project 数据库（使用上一步返回的 database_id） |
| status | select | 选项：`Pending` (default), `Ready` (blue), `Running` (yellow), `Done` (green), `Blocked` (red), `Timeout` (orange) |
| priority | select | 选项：`High` (red), `Normal` (blue), `Low` (default) |
| execution_log | rich_text | 执行日志 |

7. Task 数据库创建后，**单独 PATCH** 添加 `blocked_by` 自关联字段（self-relation 不能在创建时设置）：

```bash
curl -s -X PATCH "https://api.notion.com/v1/databases/<TASK_DB_ID>" \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Notion-Version: 2022-06-28' \
  -H 'Content-Type: application/json' \
  -d '{"properties": {"blocked_by": {"relation": {"database_id": "<TASK_DB_ID>", "single_property": {}}}}}'
```

## 步骤 5：写入配置

8. 读取当前项目的 `config.yaml`，在 `scheduler` 之前或 `workspace` 之前添加 `notion` 配置段：

```yaml
notion:
  project_db_id: "<实际 Project 数据库 ID>"
  task_db_id: "<实际 Task 数据库 ID>"
```

## 步骤 6：验证

9. 调用 `mcp__notion__API-retrieve-a-database` 验证两个数据库都能正常访问。
10. 输出完成摘要：
    - Brain 页面 URL
    - Project 数据库 ID
    - Task 数据库 ID
    - config.yaml 已更新

## 错误处理

- 任何 API 调用失败时，输出具体错误信息并停止
- 告诉用户哪一步失败了，以及如何手动完成剩余步骤

## 重要提示

- 所有 ID 必须从 API 返回值中获取，不要猜测或硬编码
- select 选项值必须与上表完全一致（大小写敏感）
- Notion Token 从 `~/.claude.json` 的 MCP 配置中读取
