#!/usr/bin/env python3
"""Self-test the conversation-shape guard: the two shapes that went red in
#3489 are rejected, the ways to repair them pass, and this repository matches
the frozen baseline."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-test-dialogue-assumptions.py")
REPOSITORY = SCRIPT.parents[2]

#: The read the tests below are written against, as the tree writes it.
READERS = '''
def _history(client, conversation_id, **params):
    return client.get(f"/topics/{conversation_id}/history", params=params).json()


def _messages(client, conversation_id):
    return client.get(
        f"/topics/{conversation_id}/history", params={"kind": "message"}
    ).json()["data"]


def _events(client, conversation_id):
    response = client.get(f"/topics/{conversation_id}/history")
    return [row for row in response.json()["data"] if row["kind"] == "event"]
'''

#: What a repaired test looks like, one line per way of saying which blocks it
#: means. None of these may be reported.
REPAIRED = '''
from sqlalchemy import select
from backend.models import Block


def test_narrowed_reads_say_which_blocks_they_mean(client, topic, ids):
    assert _history(client, topic, kind="message")["data"][0]["id"] == ids[0]
    assert [b["id"] for b in _history(client, topic, author="alice")["data"]] == ids
    assert [b["id"] for b in _messages(client, topic)] == ids
    page = _history(client, topic)
    assert len(page["data"]) < 5
    calls = [b for b in page["data"] if (b["meta"] or {}).get("tool") == "Task"]
    assert len(calls) == 1


def test_the_action_adds_nothing(client, topic):
    said = [b["id"] for b in _events(client, topic)]
    client.post(f"/topics/{topic}/messages")
    assert [b["id"] for b in _events(client, topic)] == said


def test_the_action_adds_no_blocks(session, topic):
    # The before/after comparison, taken over the database instead of the API.
    # `select(Block.id)` is a read of the same conversation, and both sides are
    # reads of it, so this is the repair, not a pinned count.
    before = set(session.scalars(select(Block.id).where(Block.conversation_id == topic)))
    session.add(Block(conversation_id=topic, kind="message"))
    after = {b.id for b in session.scalars(select(Block).where(Block.conversation_id == topic))}
    assert after == before


def test_a_db_read_narrowed_in_the_where_clause(client, session, topic):
    rows = session.scalars(
        select(Block).where(Block.conversation_id == topic, Block.kind == "message")
    ).all()
    assert len(rows) == 2


def test_a_db_read_narrowed_by_a_json_key(session, topic):
    rows = session.scalars(
        select(Block).where(
            Block.conversation_id == topic,
            Block.meta["event_type"].as_string() == "turn_failed",
        )
    ).all()
    assert len(rows) == 1
'''


class DialogueShapeGuardTests(unittest.TestCase):
    def test_this_repository_matches_its_frozen_baseline(self) -> None:
        result = _run(REPOSITORY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("通过", result.stdout)

    def test_repaired_reads_are_not_reported(self) -> None:
        findings = self._dump(REPAIRED)
        self.assertEqual(findings, [])

    def test_reading_by_position_is_rejected(self) -> None:
        self._assert_rejected(
            """
def test_takes_the_first_block(client, topic, ids):
    assert _history(client, topic)["data"][0]["id"] == ids[0]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_pinning_an_exact_length_is_rejected(self) -> None:
        self._assert_rejected(
            """
def test_counts_the_blocks(client, topic):
    assert len(_history(client, topic)["data"]) == 2
""",
            kind="length",
            repair="钉死了这段对话里有几个块",
        )

    def test_comparing_the_whole_conversation_is_rejected(self) -> None:
        # #3489's `test_chat_history.py`: a keyword search is not narrowing, so
        # the announcement the platform added answered the search too.
        self._assert_rejected(
            """
def test_holds_exactly_what_i_sent(client, topic, ids):
    assert [b["id"] for b in _history(client, topic, q="Task")["data"]] == ids[1:]
""",
            kind="whole",
            repair="拿整段对话跟一个固定的东西比",
        )

    def test_an_empty_event_slice_is_rejected(self) -> None:
        # #3489's `test_task_naming.py`: filtering down to events does not make
        # "no events" true, because events are what the platform adds.
        self._assert_rejected(
            """
def test_the_conversation_says_nothing(client, topic):
    assert _events(client, topic) == []
""",
            kind="whole",
            repair="拿整段对话跟一个固定的东西比",
        )

    def test_a_helper_that_posts_a_content_field_is_still_a_reader(self) -> None:
        # A request body mentioning `content` is not a filter. Reading it as one
        # would retire the whole helper and silently stop looking at its tests.
        self._assert_rejected(
            """
def _say_and_read(client, topic, text):
    client.post(f"/topics/{topic}/messages", json={"content": text})
    return client.get(f"/topics/{topic}/history").json()["data"]


def test_says(client, topic):
    rows = _say_and_read(client, topic, "hi")
    assert rows[0]["content"] == "hi"
""",
            kind="index",
            repair="按位置取块",
        )

    def test_an_empty_conversation_asserted_by_truthiness_is_rejected(self) -> None:
        self._assert_rejected(
            """
def test_nothing_was_said(client, topic):
    assert not _history(client, topic)["data"]
""",
            kind="whole",
            repair="拿整段对话跟一个固定的东西比",
        )

    def test_a_conversation_pinned_with_in_is_still_one_conversation(self) -> None:
        self._assert_rejected(
            """
from sqlalchemy import select
from backend.models import Block


def test_takes_the_first_block(session, topic):
    rows = session.scalars(select(Block).where(Block.topic_id.in_([topic]))).all()
    assert rows[0]["id"]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_a_conversation_pin_whose_value_has_an_id_is_not_narrowing(self) -> None:
        # `topic.id` is the argument, not the column: `.id` on the right of the
        # conversation pin must not read as a filter over the blocks.
        self._assert_rejected(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_blocks(session, topic):
    rows = session.scalars(
        select(Block).where(Block.conversation_id == topic.id)
    ).all()
    assert len(rows) == 2
""",
            kind="length",
            repair="钉死了这段对话里有几个块",
        )

    def test_a_variable_subscript_of_the_envelope_is_the_collection(self) -> None:
        # `page[field]` cannot be told from `blocks[i]` by shape. Reading it as
        # the collection keeps the empty-comparison finding; reading it as a
        # position would report an index that is not there.
        self._assert_rejected(
            """
def test_the_page_is_empty(client, topic, field):
    page = _history(client, topic)
    rows = page[field]
    assert rows == []
""",
            kind="whole",
            repair="拿整段对话跟一个固定的东西比",
        )

    def test_a_test_file_that_will_not_parse_is_not_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root, "def test_broken(:\n")
            result = _run(root)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("读不了这个测试文件", result.stderr)

    def _dump(self, source: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root, READERS + source)
            result = _run(root, "--dump")
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def _assert_rejected(self, source: str, kind: str, repair: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root, READERS + source)
            result = _run(root)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("test_example_conversation.py", result.stderr)
            self.assertIn(f"test_example_conversation.py {kind}:", result.stderr)
            self.assertIn(repair, result.stderr)
            # A baseline entry that no longer matches is a failure too, so the
            # same run has to say so: these fixtures are not the real tree.
            self.assertIn("基线过期", result.stderr)


def _write(root: Path, source: str) -> None:
    target = root / "backend" / "tests" / "integration" / "test_example_conversation.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
