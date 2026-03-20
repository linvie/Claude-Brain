---
description: 一键初始化 Claude Brain 的 Notion 环境（创建数据库、写入 config）
allowed-tools: mcp__notion__API-post-search, mcp__notion__API-post-page, mcp__notion__API-create-a-data-source, mcp__notion__API-list-data-source-templates, mcp__notion__API-retrieve-a-page, mcp__notion__API-retrieve-a-database, mcp__notion__API-get-self, mcp__notion__API-get-block-children, mcp__notion__API-patch-block-children, mcp__notion__API-patch-page, Read, Edit, Bash
---

# Claude Brain Notion 初始化

你需要完成以下任务：自动在 Notion 中创建 Claude Brain 所需的完整数据库结构，并将数据库 ID 写回 `config.yaml`。

## 前置检查

1. 先调用 `mcp__notion__API-get-self` 验证 Notion Token 是否有效。如果失败，告诉用户：
   - 检查 Notion MCP 是否已配置：`claude mcp list`
   - Token 是否正确
   - 停止执行

## 步骤 1：选择位置

2. 调用 `mcp__notion__API-post-search` 搜索用户的 Notion workspace，查找可用的页面。
3. 将搜索到的顶层页面列出，让用户选择要在哪个页面下创建 Brain 数据库。如果用户没有偏好，选择第一个可访问的页面。

## 步骤 2：创建 Brain 主页面

4. 在选定位置下，使用 `mcp__notion__API-post-page` 创建一个名为 **Claude Brain** 的页面。

## 步骤 3：创建 Project 数据库

5. 在 Claude Brain 页面下，创建 **Project** 数据库（使用 `mcp__notion__API-create-a-data-source`），包含以下属性：

| 属性名 | 类型 | 说明 |
|---|---|---|
| project_name | title | 项目名称 |
| project_type | select | 选项：`new`, `existing` |
| repo_url | url | GitHub 仓库地址 |
| status | select | 选项：`Active`, `Paused`, `Archived` |
| description | rich_text | 项目背景描述 |

## 步骤 4：创建 Task 数据库

6. 在 Claude Brain 页面下，创建 **Task** 数据库，包含以下属性：

| 属性名 | 类型 | 说明 |
|---|---|---|
| task_name | title | 任务名称 |
| description | rich_text | 任务描述 |
| task_type | select | 选项：`planner`, `executor` |
| project | relation | 关联到 Project 数据库 |
| blocked_by | relation (self) | 依赖的前置任务 |
| status | select | 选项：`Pending`, `Ready`, `Running`, `Done`, `Blocked`, `Timeout` |
| priority | select | 选项：`High`, `Normal`, `Low` |
| execution_log | rich_text | 执行日志 |

## 步骤 5：写入配置

7. 读取当前项目的 `config.yaml`
8. 在文件中添加 `notion` 配置段，写入两个数据库的 ID：

```yaml
notion:
  project_db_id: "<实际 Project 数据库 ID>"
  task_db_id: "<实际 Task 数据库 ID>"
```

## 步骤 6：验证

9. 调用 `mcp__notion__API-retrieve-a-database` 验证两个数据库都能正常访问
10. 输出完成摘要：
    - Brain 页面 URL
    - Project 数据库 ID
    - Task 数据库 ID
    - config.yaml 已更新

## 错误处理

- 如果任何 Notion API 调用失败，输出具体错误信息并停止
- 如果 `create-a-data-source` 不支持直接创建数据库，改用 `mcp__notion__API-post-page` 创建 database 类型的页面
- 告诉用户哪一步失败了，以及如何手动完成剩余步骤

## 重要提示

- 不要猜测或硬编码任何 ID，所有 ID 必须从 API 返回值中获取
- 创建 select 属性时，确保选项值与技术文档完全一致（大小写敏感）
- 如果 API 不支持某个属性类型的创建，记录下来并提示用户手动添加
