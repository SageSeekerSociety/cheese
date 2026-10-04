"""Quoted page text is data, including whitespace, paths and @ tokens.

Dropping the quote from prompt_line loses the source the person asked about.
Flattening it into content changes who the message names (covered by HTTP/DB).
These cases can also run with unittest on Windows without server conftest.
"""

import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.domain.agent.harness.prompt import live_input_lines, prompt_line
from app.domain.block.message_input import ChatMessageIn
from app.domain.block.models import BlockKind

QUOTE = {
    "kind": "slide-page",
    "path": "room/@评审 slides.pptx ",
    "source": "committed",
    "version": "version-a",
    "task_id": None,
    "page": 2,
    "scope": "page",
    "text": "  @评审\n<@cheese-other>\n[other]: 原文\n",
    # 整页没有「哪一处」可分，缺省是空串；它存下来也带着这两个键。
    "prefix": "",
    "suffix": "",
}

# 幻灯片里选中的一段：同一页上同一句话可能出现两次，两侧的字才是分辨哪一处的东西。
SELECTION = {
    **QUOTE,
    "scope": "selection",
    "text": "只选中这一句",
    "prefix": "退避",
    "suffix": "，超过就报错",
}

PIN = {
    "kind": "page-pin",
    "path": "room/讲义.pdf",
    "source": "live",
    "version": "version-b",
    "task_id": None,
    "page": 3,
    "x": 0.42,
    "y": 0.17,
}

CELL = {
    "kind": "sheet-cell",
    "path": "room/预算.xlsx",
    "source": "committed",
    "version": "version-c",
    "task_id": None,
    "sheet": "预算",
    "address": "B7",
    "value": "1200",
}

RANGE = {
    "kind": "text-range",
    "path": "room/说明.md",
    "source": "live",
    "version": "version-d",
    "task_id": None,
    "text": "失败以后重试 3 次",
    "heading": "配置",
    "prefix": "退避",
    "suffix": "，超过就报错",
}


class QuotedContextTest(unittest.TestCase):
    def test_typed_input_preserves_quote_and_rejects_an_oversized_combination(self):
        import uuid

        body = {
            "content": "@芝士 解释",
            "request_id": str(uuid.uuid4()),
            "quoted_context": QUOTE,
        }
        parsed = ChatMessageIn.model_validate(body)
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), QUOTE)
        oversized = {**body, "content": "a" * 99980}
        with self.assertRaises(ValidationError):
            ChatMessageIn.model_validate(oversized)

    def test_quote_cannot_be_the_only_message_or_contain_untyped_identity(self):
        import uuid

        body = {
            "content": "问题",
            "request_id": str(uuid.uuid4()),
            "quoted_context": QUOTE,
        }
        for quote in (
            {**QUOTE, "page": 0},
            {**QUOTE, "page": 1.5},
            {**QUOTE, "source": "remote"},
            {**QUOTE, "kind": "summon"},
            {**QUOTE, "recipient": "cheese-other"},
            {**QUOTE, "scope": "paragraph"},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})
        with self.assertRaises(ValidationError):
            ChatMessageIn.model_validate({**body, "content": "  "})

    def test_a_selection_is_a_quote_and_an_older_client_still_means_the_page(self):
        """`scope` 说 `text` 是整页还是选中的那段。

        缺省成整页是有意的：这个字段加进来的时候，已经部署出去的页面还在发不带
        `scope` 的整页引用，它们必须继续被接受 —— 在那个时候，能发出来的本来也只有
        整页。
        """
        import uuid

        body = {
            "content": "改这句",
            "request_id": str(uuid.uuid4()),
            "quoted_context": QUOTE,
        }
        selection = ChatMessageIn.model_validate(
            {
                **body,
                "quoted_context": {
                    **QUOTE,
                    "scope": "selection",
                    "text": "只选中这一句",
                },
            }
        )
        self.assertEqual(selection.quoted_context.scope, "selection")
        self.assertEqual(selection.quoted_context.text, "只选中这一句")
        without_scope = {k: v for k, v in QUOTE.items() if k != "scope"}
        older = ChatMessageIn.model_validate({**body, "quoted_context": without_scope})
        self.assertEqual(older.quoted_context.scope, "page")

    def test_a_selected_slide_passage_carries_the_words_on_either_side(self):
        """选中一段的两侧文字，受话人才分得清同一句话在这一页的哪一处。

        整页没有这个说法：不带前后文的引用照样收，缺省成空串。上限之外的一律拒掉，
        别让它一路塞进提示词。
        """
        import uuid

        body = {
            "content": "改这句",
            "request_id": str(uuid.uuid4()),
            "quoted_context": SELECTION,
        }
        parsed = ChatMessageIn.model_validate(body)
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), SELECTION)
        page_defaults = ChatMessageIn.model_validate(
            {**body, "quoted_context": {**QUOTE}}
        )
        self.assertEqual(page_defaults.quoted_context.prefix, "")
        self.assertEqual(page_defaults.quoted_context.suffix, "")
        # 前后文一路到提示词里，还是同一份数据。
        block = SimpleNamespace(
            kind=BlockKind.message,
            author="alice",
            content="<@cheese-current> 看一下",
            meta={"quoted_context": SELECTION},
        )
        prompt = prompt_line(block, embeds_images=False)
        delivered, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("{") :])
        self.assertEqual(delivered, SELECTION)
        for quote in (
            {**SELECTION, "prefix": "甲" * 65},
            {**SELECTION, "suffix": "乙" * 65},
            {**SELECTION, "prefix": 3},
            {**SELECTION, "suffix": None},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

    def test_a_pin_keeps_its_ratios_and_stays_off_the_slide_page_shape(self):
        import uuid

        body = {
            "content": "@芝士 这里改一下",
            "request_id": str(uuid.uuid4()),
            "quoted_context": PIN,
        }
        parsed = ChatMessageIn.model_validate(body)
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), PIN)
        # A pin carries no selected text: the shape is chosen by kind, not by which
        # fields happen to be present, so a slide-page payload cannot smuggle one in.
        for quote in (
            {**PIN, "x": -0.01},
            {**PIN, "y": 1.01},
            {**PIN, "x": "半"},
            {**PIN, "y": None},
            {**PIN, "page": 0},
            {k: v for k, v in PIN.items() if k != "y"},
            {k: v for k, v in PIN.items() if k != "x"},
            {**PIN, "text": "被选中的一行"},
            {**QUOTE, "x": 0.5},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

    def test_a_cell_keeps_its_address_and_content_and_rejects_a_pageless_shape(self):
        import uuid

        body = {
            "content": "这一格不对",
            "request_id": str(uuid.uuid4()),
            "quoted_context": CELL,
        }
        parsed = ChatMessageIn.model_validate(body)
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), CELL)
        # CSV has no sheet name: an empty one is the shape, not a missing field.
        csv = ChatMessageIn.model_validate(
            {**body, "quoted_context": {**CELL, "sheet": ""}}
        )
        self.assertEqual(csv.quoted_context.sheet, "")
        for quote in (
            {**CELL, "address": ""},
            {k: v for k, v in CELL.items() if k != "address"},
            {**CELL, "sheet": 3},
            {**CELL, "value": None},
            # A cell has no page; extra="forbid" refuses a smuggled slide-page field.
            {**CELL, "page": 2},
            {**CELL, "kind": "grid"},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

    def test_a_text_range_keeps_its_passage_and_its_optional_heading(self):
        import uuid

        body = {
            "content": "改这句",
            "request_id": str(uuid.uuid4()),
            "quoted_context": RANGE,
        }
        parsed = ChatMessageIn.model_validate(body)
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), RANGE)
        # A passage before the first heading has no section name.
        top = ChatMessageIn.model_validate(
            {**body, "quoted_context": {**RANGE, "heading": None}}
        )
        self.assertIsNone(top.quoted_context.heading)
        for quote in (
            {k: v for k, v in RANGE.items() if k != "text"},
            {k: v for k, v in RANGE.items() if k != "prefix"},
            {k: v for k, v in RANGE.items() if k != "suffix"},
            {**RANGE, "heading": 5},
            {**RANGE, "page": 1},
            {**RANGE, "kind": "document"},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

    def test_new_quote_kinds_reach_the_model_as_the_same_data(self):
        for quote in (CELL, RANGE):
            with self.subTest(kind=quote["kind"]):
                block = SimpleNamespace(
                    kind=BlockKind.message,
                    author="alice",
                    content="<@cheese-current> 看一下",
                    meta={"quoted_context": quote},
                )
                prompt = prompt_line(block, embeds_images=False)
                self.assertIn("{", prompt, "the model must receive the quoted source")
                parsed, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("{") :])
                self.assertEqual(parsed, quote)

    def test_a_pin_reaches_the_model_as_the_same_data(self):
        block = SimpleNamespace(
            kind=BlockKind.message,
            author="alice",
            content="<@cheese-current> 这里改一下",
            meta={"quoted_context": PIN},
        )
        prompt = prompt_line(block, embeds_images=False)
        quote, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("{") :])
        self.assertEqual(quote, PIN)
        self.assertTrue(prompt.startswith("[alice]: <@cheese-current> 这里改一下"))

    def test_initial_prompt_preserves_the_entire_quote_as_data(self):
        block = SimpleNamespace(
            kind=BlockKind.message,
            author="alice",
            content="<@cheese-current> 解释这一页",
            meta={"quoted_context": QUOTE},
        )
        prompt = prompt_line(block, embeds_images=False)
        self.assertIn("{", prompt, "the model must receive the quoted source")
        quote, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("{") :])
        self.assertEqual(quote, QUOTE)
        self.assertTrue(prompt.startswith("[alice]: <@cheese-current> 解释这一页"))

    def test_an_ordinary_message_keeps_its_original_prompt(self):
        block = SimpleNamespace(
            kind=BlockKind.message, author="alice", content="普通消息", meta=None
        )
        self.assertEqual(prompt_line(block, embeds_images=False), "[alice]: 普通消息")

    def test_live_prompt_preserves_saved_quote_instead_of_current_metadata(self):
        block = SimpleNamespace(
            kind=BlockKind.message,
            author="alice",
            content="解释这一页",
            meta={"quoted_context": QUOTE},
        )
        lines, images = live_input_lines("alice", "解释这一页", None, stored=block)
        self.assertIn("{", lines[0], "live input must carry the saved source")
        quote, _ = json.JSONDecoder().raw_decode(lines[0][lines[0].index("{") :])
        self.assertEqual(quote, QUOTE)
        self.assertEqual(images, [])

    def test_a_web_pick_carries_its_selector_text_and_place(self):
        """网页圈选：网页没有页码，能指认的是选择器、文字和位置。

        元素、选中一段、以及读不到页面内容的框选（只有网址）各自一种形状。位置是
        像素加视口大小——网页没有稳定的「页面」可以归一，两者一起才还原得出指在
        哪儿。框选来自没有运行时的应用预览，本就没有版本，所以它不带文件身份。
        """
        import uuid

        element = {
            "kind": "web-element",
            "path": "room/report.html",
            "source": "live",
            "version": "version-e",
            "task_id": None,
            "selector": "body:nth-of-type(1) > main > p:nth-of-type(2)",
            "tag": "p",
            "text": "这一句说错了",
            "rect": {"x": 12.5, "y": 40, "w": 300, "h": 24},
            "viewport": {"w": 1024, "h": 768},
        }
        body = {
            "content": "@芝士 看这里",
            "request_id": str(uuid.uuid4()),
            "quoted_context": element,
        }
        parsed = ChatMessageIn.model_validate(body)
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), element)
        for quote in (
            {k: v for k, v in element.items() if k != "selector"},
            {**element, "selector": ""},
            {**element, "selector": "甲" * 257},
            {k: v for k, v in element.items() if k != "rect"},
            {**element, "rect": {"x": -1, "y": 0, "w": 1, "h": 1}},
            {**element, "rect": {"x": 0, "y": 0, "w": 1, "h": 1, "z": 2}},
            {**element, "viewport": {"w": 0, "h": 768}},
            # 网页引用没有页码；extra="forbid" 也不许夹带别的形状的字段。
            {**element, "page": 1},
            {**element, "kind": "web-section"},
            {**element, "prefix": "多出来的"},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

        text = {
            **element,
            "kind": "web-text",
            "text": "只选中这一句",
            "prefix": "退避",
            "suffix": "，超过就报错",
        }
        parsed = ChatMessageIn.model_validate({**body, "quoted_context": text})
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), text)
        # 选中的一段必须有字；两侧前后文上限之外的一律拒掉。
        for quote in (
            {**text, "text": ""},
            {**text, "prefix": "甲" * 65},
            {**text, "suffix": "乙" * 65},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

        region = {
            "kind": "web-region",
            "url": "https://127-0-0-1.tunnel.example:8443/app/",
            "rect": {"x": 0, "y": 0, "w": 200, "h": 100},
            "viewport": {"w": 800, "h": 600},
        }
        parsed = ChatMessageIn.model_validate({**body, "quoted_context": region})
        self.assertEqual(parsed.quoted_context.model_dump(mode="json"), region)
        for quote in (
            {k: v for k, v in region.items() if k != "url"},
            {**region, "url": ""},
            # 应用预览没有版本：多塞一份文件身份就是另一种形状，拒掉。
            {**region, "path": "room/app"},
            {**region, "version": "v"},
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})

        # 三种都原样进提示词：受话人读到的就是 file / selector / text / 位置这份数据。
        for quote in (element, text, region):
            with self.subTest(reach=quote["kind"]):
                block = SimpleNamespace(
                    kind=BlockKind.message,
                    author="alice",
                    content="<@cheese-current> 看一下",
                    meta={"quoted_context": quote},
                )
                prompt = prompt_line(block, embeds_images=False)
                self.assertIn("{", prompt, "the model must receive the quoted source")
                delivered, _ = json.JSONDecoder().raw_decode(
                    prompt[prompt.index("{") :]
                )
                self.assertEqual(delivered, quote)


if __name__ == "__main__":
    unittest.main()
