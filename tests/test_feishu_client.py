"""飞书客户端单元测试 — markdown 转换、卡片构建。

覆盖历史 bug：
- 飞书 markdown 不支持 ## 标题、| 表格 |、> 引用
- 卡片 JSON 必须包含 update_multi: true
- 长内容需要分段（单 markdown 元素限 10000 字符）
"""

import json

from brain.channels.feishu.client import (
    FeishuClient,
    _optimize_markdown,
    _split_markdown,
)


class TestOptimizeMarkdown:
    """_optimize_markdown: 飞书不支持的 markdown 语法转换。"""

    def test_heading_to_bold(self):
        assert _optimize_markdown("## 标题") == "**标题**"
        assert _optimize_markdown("### 三级标题") == "**三级标题**"
        assert _optimize_markdown("# H1") == "**H1**"

    def test_multiple_headings(self):
        text = "## First\nsome text\n## Second"
        result = _optimize_markdown(text)
        assert "**First**" in result
        assert "**Second**" in result
        assert "##" not in result

    def test_blockquote_removed(self):
        assert _optimize_markdown("> 引用内容") == "引用内容"
        assert _optimize_markdown(">无空格") == "无空格"

    def test_html_details_summary(self):
        text = "<details><summary>展开</summary>内容</details>"
        result = _optimize_markdown(text)
        assert "<details>" not in result
        assert "<summary>" not in result
        assert "**展开**" in result

    def test_table_to_list(self):
        text = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
        result = _optimize_markdown(text)
        assert "|" not in result or "- " in result
        assert "Alice" in result
        assert "Bob" in result

    def test_table_with_empty_cells(self):
        text = "| A | B |\n|---|---|\n| x |  |"
        result = _optimize_markdown(text)
        # 空 cell 应被过滤
        assert "x" in result

    def test_plain_text_unchanged(self):
        text = "普通文本\n\n**加粗** *斜体* `代码`"
        assert _optimize_markdown(text) == text

    def test_code_block_preserved(self):
        text = "```python\ndef foo():\n    pass\n```"
        assert _optimize_markdown(text) == text

    def test_mixed_content(self):
        text = "## 标题\n\n> 引用\n\n普通段落\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        result = _optimize_markdown(text)
        assert "##" not in result
        assert "**标题**" in result
        assert "> " not in result


class TestSplitMarkdown:
    """_split_markdown: 按段落分割长内容。"""

    def test_short_text_no_split(self):
        text = "短文本"
        assert _split_markdown(text, 100) == ["短文本"]

    def test_split_at_paragraph_boundary(self):
        text = "段落1\n\n段落2\n\n段落3"
        chunks = _split_markdown(text, 10)
        assert len(chunks) >= 2
        # 所有原始内容都保留
        joined = "\n\n".join(chunks)
        assert "段落1" in joined
        assert "段落3" in joined

    def test_each_chunk_within_limit(self):
        text = "\n\n".join([f"段落{i} " * 20 for i in range(10)])
        chunks = _split_markdown(text, 200)
        for chunk in chunks:
            assert len(chunk) <= 200

    def test_single_long_paragraph(self):
        """单段落超长时，至少返回一个 chunk。"""
        text = "x" * 500
        chunks = _split_markdown(text, 100)
        assert len(chunks) >= 1


class TestBuildCard:
    """_build_card: 构建飞书 Interactive Card JSON。"""

    def test_card_has_update_multi(self):
        """卡片必须包含 update_multi: true，否则 patch API 无法更新。"""
        card_json = FeishuClient._build_card("test")
        card = json.loads(card_json)
        assert card["config"]["update_multi"] is True

    def test_card_with_title(self):
        card_json = FeishuClient._build_card("内容", title="标题")
        card = json.loads(card_json)
        elements = card["elements"]
        # 第一个元素是标题 markdown，第二个是 hr
        assert elements[0]["tag"] == "markdown"
        assert "标题" in elements[0]["content"]
        assert elements[1]["tag"] == "hr"

    def test_card_without_title(self):
        card_json = FeishuClient._build_card("内容")
        card = json.loads(card_json)
        elements = card["elements"]
        assert elements[0]["tag"] == "markdown"
        assert elements[0]["content"] == "内容"

    def test_long_content_splits(self):
        """超长内容应分为多个 markdown 元素（单元素限 9000 字符）。"""
        long_text = "\n\n".join(["段落内容 " * 500 for _ in range(5)])
        card_json = FeishuClient._build_card(long_text)
        card = json.loads(card_json)
        md_elements = [e for e in card["elements"] if e["tag"] == "markdown"]
        assert len(md_elements) > 1

    def test_card_applies_markdown_optimization(self):
        """卡片构建时应自动优化 markdown。"""
        card_json = FeishuClient._build_card("## 标题\n\n> 引用")
        card = json.loads(card_json)
        content = card["elements"][0]["content"]
        assert "##" not in content
        assert "**标题**" in content
