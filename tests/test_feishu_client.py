"""飞书客户端单元测试 — markdown 转换、卡片构建。

覆盖历史 bug：
- 飞书卡片 schema 2.0 标题降级（H1→H4，H2~→H5）
- 卡片 JSON 必须包含 update_multi: true 和 schema 2.0
- 长内容需要分段（单 markdown 元素限 10000 字符）
- 表格渲染失败时降级为列表格式
"""

import json
from unittest.mock import patch

import lark_oapi as lark

from brain.channels.feishu.adapter import _platform_to_domain
from brain.channels.feishu.client import (
    FeishuClient,
    _optimize_markdown,
    _split_markdown,
    _table_to_list,
)


class TestOptimizeMarkdown:
    """_optimize_markdown: schema 2.0 适配（标题降级，保留表格/引用）。"""

    def test_heading_downgrade(self):
        assert _optimize_markdown("# H1") == "#### H1"
        assert _optimize_markdown("## H2") == "##### H2"
        assert _optimize_markdown("### H3") == "##### H3"

    def test_multiple_headings(self):
        text = "## First\nsome text\n## Second"
        result = _optimize_markdown(text)
        assert "##### First" in result
        assert "##### Second" in result
        assert "**" not in result

    def test_blockquote_preserved(self):
        """schema 2.0 支持引用，保留 > 前缀。"""
        assert _optimize_markdown("> 引用内容") == "> 引用内容"
        assert _optimize_markdown("> 多行\n> 引用") == "> 多行\n> 引用"

    def test_html_details_summary(self):
        text = "<details><summary>展开</summary>内容</details>"
        result = _optimize_markdown(text)
        assert "<details>" not in result
        assert "<summary>" not in result
        assert "**展开**" in result

    def test_table_preserved(self):
        """schema 2.0 支持表格，保留 | 语法。"""
        text = "| Name | Age |\n|---|---|\n| Alice | 30 |"
        result = _optimize_markdown(text)
        assert "|" in result
        assert "Alice" in result
        assert "- " not in result

    def test_plain_text_unchanged(self):
        text = "普通文本\n\n**加粗** *斜体* `代码`"
        assert _optimize_markdown(text) == text

    def test_code_block_preserved(self):
        text = "```python\ndef foo():\n    pass\n```"
        assert _optimize_markdown(text) == text

    def test_mixed_content(self):
        text = "## 标题\n\n> 引用\n\n普通段落\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        result = _optimize_markdown(text)
        assert "##### 标题" in result
        assert "> 引用" in result
        assert "| A | B |" in result


class TestTableToList:
    """_table_to_list: 表格降级为列表格式。"""

    def test_basic_table(self):
        text = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
        result = _table_to_list(text)
        assert "- " in result
        assert "Alice" in result
        assert "Bob" in result

    def test_table_with_empty_cells(self):
        text = "| A | B |\n|---|---|\n| x |  |"
        result = _table_to_list(text)
        assert "x" in result

    def test_non_table_text_unchanged(self):
        text = "普通文本\n\n代码块"
        assert _table_to_list(text) == text

    def test_mixed_table_and_text(self):
        text = "前文\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n后文"
        result = _table_to_list(text)
        assert "前文" in result
        assert "后文" in result
        assert "- " in result


class TestSplitMarkdown:
    """_split_markdown: 按段落分割长内容。"""

    def test_short_text_no_split(self):
        text = "短文本"
        assert _split_markdown(text, 100) == ["短文本"]

    def test_split_at_paragraph_boundary(self):
        text = "段落1\n\n段落2\n\n段落3"
        chunks = _split_markdown(text, 10)
        assert len(chunks) >= 2
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
    """_build_card: 构建飞书 Interactive Card JSON（schema 2.0）。"""

    def test_card_schema_2_0(self):
        card = json.loads(FeishuClient._build_card("test"))
        assert card["schema"] == "2.0"

    def test_card_wide_screen_mode(self):
        card = json.loads(FeishuClient._build_card("test"))
        assert card["config"]["wide_screen_mode"] is True
        assert card["config"]["update_multi"] is True

    def test_card_body_elements_structure(self):
        """schema 2.0 elements 在 body.elements 下。"""
        card = json.loads(FeishuClient._build_card("test"))
        assert "body" in card
        assert "elements" in card["body"]
        assert card["body"]["elements"][0]["tag"] == "markdown"

    def test_card_with_title(self):
        card = json.loads(FeishuClient._build_card("内容", title="标题"))
        elements = card["body"]["elements"]
        assert elements[0]["tag"] == "markdown"
        assert "#### 标题" in elements[0]["content"]
        assert elements[1]["tag"] == "hr"

    def test_card_without_title(self):
        card = json.loads(FeishuClient._build_card("内容"))
        elements = card["body"]["elements"]
        assert elements[0]["content"] == "内容"

    def test_card_with_footer(self):
        card = json.loads(FeishuClient._build_card("内容", footer="耗时 3s · model"))
        elements = card["body"]["elements"]
        footer_el = elements[-1]
        assert footer_el["tag"] == "markdown"
        assert footer_el["text_size"] == "notation"
        assert "耗时 3s" in footer_el["content"]
        # footer 前有 hr
        assert elements[-2]["tag"] == "hr"

    def test_card_without_footer(self):
        card = json.loads(FeishuClient._build_card("内容"))
        elements = card["body"]["elements"]
        assert len(elements) == 1
        assert elements[0]["tag"] == "markdown"

    def test_long_content_splits(self):
        """超长内容应分为多个 markdown 元素。"""
        long_text = "\n\n".join(["段落内容 " * 500 for _ in range(5)])
        card = json.loads(FeishuClient._build_card(long_text))
        md_elements = [e for e in card["body"]["elements"] if e["tag"] == "markdown"]
        assert len(md_elements) > 1

    def test_card_applies_heading_downgrade(self):
        """卡片构建时应用标题降级。"""
        card = json.loads(FeishuClient._build_card("## 标题\n\n> 引用"))
        content = card["body"]["elements"][0]["content"]
        assert "##### 标题" in content
        assert "> 引用" in content
        assert "**" not in content


class TestPlatformToDomain:
    """_platform_to_domain: 平台字符串转换为 lark_oapi 域名。"""

    def test_feishu_platform(self):
        assert _platform_to_domain("feishu") == lark.FEISHU_DOMAIN

    def test_lark_platform(self):
        assert _platform_to_domain("lark") == lark.LARK_DOMAIN

    def test_unknown_defaults_to_feishu(self):
        assert _platform_to_domain("unknown") == lark.FEISHU_DOMAIN

    def test_empty_defaults_to_feishu(self):
        assert _platform_to_domain("") == lark.FEISHU_DOMAIN


class TestFeishuClientDomain:
    """FeishuClient: domain 参数传递到 lark SDK。"""

    @patch("brain.channels.feishu.client.lark.Client.builder")
    def test_default_domain_no_extra_call(self, mock_builder):
        """默认 feishu 域名时不调用 .domain()。"""
        chain = mock_builder.return_value
        chain.app_id.return_value = chain
        chain.app_secret.return_value = chain
        chain.log_level.return_value = chain
        chain.build.return_value = "client"

        FeishuClient("id", "secret")
        chain.domain.assert_not_called()

    @patch("brain.channels.feishu.client.lark.Client.builder")
    def test_lark_domain_calls_domain(self, mock_builder):
        """Lark 域名时应调用 .domain()。"""
        chain = mock_builder.return_value
        chain.app_id.return_value = chain
        chain.app_secret.return_value = chain
        chain.log_level.return_value = chain
        chain.domain.return_value = chain
        chain.build.return_value = "client"

        FeishuClient("id", "secret", domain=lark.LARK_DOMAIN)
        chain.domain.assert_called_once_with(lark.LARK_DOMAIN)


class TestFeishuAdapterPlatform:
    """FeishuAdapter: platform 参数影响域名和 IncomingMessage.platform。"""

    def test_adapter_default_feishu(self):
        from brain.channels.feishu.adapter import FeishuAdapter

        with patch("brain.channels.feishu.adapter.FeishuClient"):
            adapter = FeishuAdapter("id", "secret")
            assert adapter._platform == "feishu"
            assert adapter._domain == lark.FEISHU_DOMAIN

    def test_adapter_lark_platform(self):
        from brain.channels.feishu.adapter import FeishuAdapter

        with patch("brain.channels.feishu.adapter.FeishuClient"):
            adapter = FeishuAdapter("id", "secret", platform="lark")
            assert adapter._platform == "lark"
            assert adapter._domain == lark.LARK_DOMAIN


class TestFeishuPlatformConfig:
    """FEISHU_PLATFORM config constant."""

    def test_config_default(self):
        """默认 platform 为 feishu。"""
        from brain import config as cfg_mod
        # FEISHU_PLATFORM should exist and default to "feishu"
        assert hasattr(cfg_mod, "FEISHU_PLATFORM")
        # The actual loaded value depends on config.yaml, but the constant exists
        assert cfg_mod.FEISHU_PLATFORM in ("feishu", "lark")
