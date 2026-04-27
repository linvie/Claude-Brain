---
name: dev
description: 在终端打开 Claude Code 直接介入指定项目开发。把项目名解析成 workspace 路径，回一条可复制的进入命令。当用户说"我要介入 X 项目"、"打开 CC 开发 X"、"/dev X"时使用。
argument-hint: "<项目名称>"
allowed-tools: mcp__notion__API-post-search, mcp__notion__API-query-data-source, Bash
---

# Dev Skill — 介入项目开发

让用户在本机直接打开 Claude Code 介入某个项目的开发，绕开 Notion executor 派发链路，适合复杂任务、需要交互式迭代的场景。

## 已知 ID

- Project 数据库：`33ee370a-a187-8139-aed5-d42e94fc13b5`
- workspace 根目录：`~/.ccbrain/workspaces/`
- workspace 路径规则：`~/.ccbrain/workspaces/<project_id>`（project_id = Notion 页面 UUID，带 dashes）

## 工作流程

### 1. 解析项目名

从用户输入提取项目名（如 "CCBrain"、"我要介入 CCBrain"），调 Notion 搜索：

```json
mcp__notion__API-post-search
query: "<关键词>"
filter: {"property": "object", "value": "page"}
```

从结果中筛选 `parent.database_id == "33ee370a-a187-8139-aed5-d42e94fc13b5"` 的页面（即 Project DB 下的项目）。

### 2. 处理匹配结果

| 匹配数 | 处理 |
|--------|------|
| 0 | 告诉用户找不到，列出 Project DB 中所有项目名供参考 |
| 1 | 直接进入第 3 步 |
| 多个 | 列出所有匹配（项目名 + status + 简短 description），让用户回复确认是哪个 |

如果命中项目 `status` 是 `Paused` 或 `Archived`，提示一下但仍允许继续（用户可能就是想恢复一下）。

### 3. 检查 workspace 是否存在

构造路径：`~/.ccbrain/workspaces/<project_id>`，用 Bash 验证：

```bash
test -d ~/.ccbrain/workspaces/<project_id> && echo "exists" || echo "missing"
```

- **存在** → 第 4 步
- **不存在** → 提示用户："这个项目还没有 workspace。可以先在 Notion 创建一个 executor 任务（哪怕是 noop）让 Brain bootstrap 一下，或者告诉我让我手动调 prepare_workspace。"

### 4. 输出可复制命令

回复格式（飞书会渲染成 Markdown 卡片，代码块可一键复制）：

````
✅ 找到项目：<project_name>（status=<status>）

终端粘贴以下命令进入开发：

```bash
cd ~/.ccbrain/workspaces/<project_id> && claude
```

提示：
- 你的会话独立于 Brain daemon 派发的 executor session，互不影响
- 完成后正常 commit/push 即可，不需要回 Notion 同步状态
````

## 注意事项

- 项目名可能包含中文/特殊字符，搜索时直接用原文 query，Notion 模糊匹配
- 不要尝试自动执行 `cd && claude`——这是要用户在自己终端里跑的命令，不是飞书 session 里的
- 调用前需通过 `ToolSearch` 加载 `mcp__notion__API-post-search` 的 schema

## 相关待办（未实现）

- [ ] `ccbrain dev <project>` 本地 CLI：把这个流程封装成命令行工具，省一次复制粘贴。已存到 Notion CCBrain 项目的 Pending 任务里
- [ ] 接续上一个 executor session：需要在 Notion task 上记录 session_id（目前 Brain 是 per-channel session，不是 per-task）
