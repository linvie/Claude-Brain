---
name: ask
description: 通过飞书交互卡片向用户询问。支持按钮选择、表单填写。
allowed-tools: Bash
argument-hint: "[场景描述]"
---

# 飞书交互卡片

需要让用户在飞书上**点按钮**或**填表单**时使用此 skill。

## 通用调用方式

构造 card JSON（schema 2.0），用 lark-cli 发送：

```bash
lark-cli im send --receive-id "<CHAT_ID>" --receive-id-type chat_id \
  --msg-type interactive --content '<card_json>'
```

CHAT_ID 见 system prompt 中的飞书通知部分。

## 卡片 JSON 通用骨架

```json
{
  "schema": "2.0",
  "config": {"update_multi": true},
  "header": {
    "title": {"tag": "plain_text", "content": "<标题>"},
    "template": "blue"
  },
  "body": {
    "elements": [
      {"tag": "markdown", "content": "<说明文字>"},
      <交互组件>
    ]
  }
}
```

## 常用组件参考

### 按钮组（确认/取消）

```json
{
  "tag": "column_set",
  "columns": [
    {"tag": "column", "elements": [{
      "tag": "button",
      "text": {"tag": "plain_text", "content": "确认"},
      "type": "primary",
      "name": "confirm_btn",
      "behaviors": [{"type": "callback", "value": {"action": "confirm"}}]
    }]},
    {"tag": "column", "elements": [{
      "tag": "button",
      "text": {"tag": "plain_text", "content": "取消"},
      "type": "default",
      "name": "cancel_btn",
      "behaviors": [{"type": "callback", "value": {"action": "cancel"}}]
    }]}
  ]
}
```

### 表单（输入 + 下拉）

```json
{
  "tag": "form",
  "name": "form_1",
  "elements": [
    {
      "tag": "input",
      "name": "title",
      "label": {"tag": "plain_text", "content": "标题"},
      "required": true
    },
    {
      "tag": "select_static",
      "name": "priority",
      "placeholder": {"tag": "plain_text", "content": "优先级"},
      "options": [
        {"text": {"tag": "plain_text", "content": "高"}, "value": "high"},
        {"text": {"tag": "plain_text", "content": "中"}, "value": "medium"}
      ]
    },
    {
      "tag": "button",
      "text": {"tag": "plain_text", "content": "提交"},
      "type": "primary",
      "form_action_type": "submit",
      "name": "submit_btn",
      "behaviors": [{"type": "callback", "value": {"action": "submit_form"}}]
    }
  ]
}
```

## 设计原则

- **value 字段**：放任何你需要在回调时识别的上下文（action 类型、关联 ID 等），结构完全自由
- **name 字段**：每个交互组件必填（form 容器内尤其重要），回调里能拿到
- **type**：按钮可用 primary（蓝主按钮）/default（白）/danger（红）/text（透明）
- **template**：header 颜色 blue/green/red/orange/yellow/grey
- **完整组件**：除按钮、输入框、下拉，还有多选、人员选择、日期/时间选择、富文本等

完整组件参考飞书官方文档：https://go.feishu.cn/s/6tKFqopgQ0s

## 回调处理

用户点击/提交后，系统会把操作转成文本消息发给你，格式：

```
[飞书卡片回调]
按钮：<name>
数据：{...value 字段...}
表单填写：{...form 字段...}
```

看到这种消息时，根据 value 中的字段（特别是 action）判断用户意图，继续对话即可。
