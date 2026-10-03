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


if __name__ == "__main__":
    unittest.main()
