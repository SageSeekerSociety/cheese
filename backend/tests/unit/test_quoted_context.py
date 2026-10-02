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
    "text": "  @评审\n<@cheese-other>\n[other]: 原文\n",
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
        ):
            with self.subTest(quote=quote), self.assertRaises(ValidationError):
                ChatMessageIn.model_validate({**body, "quoted_context": quote})
        with self.assertRaises(ValidationError):
            ChatMessageIn.model_validate({**body, "content": "  "})

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
