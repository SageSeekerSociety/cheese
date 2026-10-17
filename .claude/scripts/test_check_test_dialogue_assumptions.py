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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(
                root,
                READERS
                + """
def test_the_page_is_empty(client, topic, field):
    page = _history(client, topic)
    rows = page[field]
    assert rows == []
""",
            )
            result = _run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("whole:", result.stderr)
        self.assertNotIn("index:", result.stderr)

    def test_a_helper_defined_before_the_one_it_delegates_to_is_still_a_reader(
        self,
    ) -> None:
        # Reading a reader's result is reading the conversation. When the
        # delegate is written above, classifying it used to reach the helper
        # while the delegate was still being classified; the helper then got
        # cached as an ordinary function and every test using it went unseen.
        self._assert_rejected(
            """
def _fetch(client, topic):
    return client.get(f"/topics/{topic}/history").json()["data"]


def _read(client, topic):
    return _fetch(client, topic)


def test_takes_the_first_block(client, topic):
    rows = _read(client, topic)
    assert rows[0]["id"]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_a_qualified_select_is_still_a_read_of_the_conversation(self) -> None:
        # `sqlalchemy.select(Block)` is the same query as `select(Block)`; only
        # the name it is reached through differs.
        self._assert_rejected(
            """
import sqlalchemy
from backend.models import Block


def test_takes_the_first_block(session, topic):
    rows = session.scalars(
        sqlalchemy.select(Block).where(Block.topic_id == topic)
    ).all()
    assert rows[0]["id"]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_testing_every_block_is_not_the_whole_conversation(self) -> None:
        # `all(...)` is a bool. Reading it as the collection would report a
        # repair — the predicate has already said which blocks it means.
        findings = self._dump(
            """
def test_no_platform_block_is_a_message(client, topic):
    rows = _history(client, topic)["data"]
    assert not all(b["kind"] == "message" for b in rows)
"""
        )
        self.assertEqual(findings, [])

    def test_a_helper_that_narrows_a_reader_is_not_a_reader(self) -> None:
        # The returned comprehension filtered the blocks, so the helper does
        # not hand back the timeline. Reading a reader somewhere inside the
        # returned expression is not the same as returning its result.
        findings = self._dump(
            """
def _proposals(client, topic):
    return [
        b for b in _history(client, topic)["data"] if b.get("event_type") == "proposal"
    ]


def test_makes_one_proposal(client, topic):
    proposals = _proposals(client, topic)
    assert len(proposals) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_rebinding_a_name_in_a_nested_function_ends_the_conversation(
        self,
    ) -> None:
        # The inner function shadows the outer `rows`; the outer conversation
        # is not what its assertions are about.
        findings = self._dump(
            """
def test_inner_rows_are_not_the_conversation(client, topic):
    rows = _history(client, topic)["data"]
    def check(rows):
        assert rows[0] == 1
    check([1, 2])
"""
        )
        self.assertEqual(findings, [])

    def test_reordering_the_blocks_keeps_them_held(self) -> None:
        # `sorted(rows, key=…)` hands the same blocks on, reordered. Dropping
        # the name here would stop seeing the positional read on the next line.
        self._assert_rejected(
            """
def test_first_by_id(client, topic):
    rows = _history(client, topic)["data"]
    rows = sorted(rows, key=lambda block: block["id"])
    assert rows[0]["id"]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_a_filter_on_the_event_kind_is_not_narrowing(self) -> None:
        # `filter_by(kind="event")` names exactly the slice announcements land
        # in, so the count is pinned to how much the platform says.
        self._assert_rejected(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_events(session, topic):
    rows = session.scalars(
        select(Block)
        .where(Block.conversation_id == topic)
        .filter_by(kind="event")
    ).all()
    assert len(rows) == 2
""",
            kind="length",
            repair="钉死了这段对话里有几个块",
        )

    def test_a_content_filter_narrows_the_query(self) -> None:
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_finds_the_message(session, topic):
    rows = session.scalars(
        select(Block).where(
            Block.conversation_id == topic,
            Block.content.startswith("B 组第 7 行"),
        )
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_a_reversed_key_comparison_narrows_the_query(self) -> None:
        # `"message" == Block.kind` is the same filter as `Block.kind == …`.
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_finds_the_message(session, topic):
    rows = session.scalars(
        select(Block).where(Block.conversation_id == topic, "message" == Block.kind)
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_a_helper_that_asks_for_a_slice_is_not_a_reader(self) -> None:
        # `_messages` hands `kind="message"` on to the reader below it, so what
        # it returns is the test's own slice. Judging it by what the callee
        # returns, instead of by what this call asked for, reports a repaired
        # test — and the call site is where the narrowing is written.
        findings = self._dump(
            """
def _history(client, conversation_id, **params):
    return client.get(
        f"/topics/{conversation_id}/history", params=params
    ).json()["data"]


def _messages(client, conversation_id):
    return _history(client, conversation_id, kind="message")


def test_counts_the_messages(client, topic):
    assert len(_messages(client, topic)) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_a_helper_run_through_asyncio_is_still_a_reader(self) -> None:
        # `asyncio.run(read())` returns what `read` returned, and the envelope
        # key is peeled off after that. Both layers used to hide the read.
        self._assert_rejected(
            """
import asyncio


def _blocks(client, conversation_id):
    async def read():
        return (await client.get(f"/topics/{conversation_id}/history")).json()

    return asyncio.run(read())["data"]


def test_takes_the_first_block(client, topic):
    assert _blocks(client, topic)[0]["id"]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_a_json_key_the_guard_does_not_know_still_narrows(self) -> None:
        # `meta["tool"]` is not one of the keys this file lists, but it is
        # still a key of `Block.meta`: only tool calls come back.
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_tasks(session, topic):
    rows = session.scalars(
        select(Block).where(
            Block.conversation_id == topic,
            Block.meta["tool"].as_string() == "Task",
        )
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_leaving_the_event_slice_out_narrows_the_query(self) -> None:
        # `kind != "event"` names the events only to exclude them: no
        # announcement joins what this reads.
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_non_events(session, topic):
    rows = session.scalars(
        select(Block).where(Block.conversation_id == topic, Block.kind != "event")
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_a_filter_that_also_excludes_a_block_is_still_the_event_slice(self) -> None:
        # `kind == "event" and id != first` is the event slice with one block
        # taken out, not a filter that leaves the events out.
        self._assert_rejected(
            """
def test_the_other_events(client, topic, first):
    rows = [
        b["id"]
        for b in _history(client, topic)["data"]
        if b["kind"] == "event" and b["id"] != first
    ]
    assert rows == ["said"]
""",
            kind="whole",
            repair="拿整段对话跟一个固定的东西比",
        )

    def test_a_filter_by_a_row_key_narrows_the_query(self) -> None:
        # `filter_by(content=…)` says which block just as `.where(…)` does.
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_finds_the_message(session, topic):
    rows = session.scalars(
        select(Block).where(Block.conversation_id == topic).filter_by(content="hi")
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_excluding_the_events_with_notin_narrows_the_query(self) -> None:
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_non_events(session, topic):
    rows = session.scalars(
        select(Block).where(
            Block.conversation_id == topic, Block.kind.notin_(["event"])
        )
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_excluding_the_events_with_is_not_narrows_the_query(self) -> None:
        findings = self._dump(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_non_events(session, topic):
    rows = session.scalars(
        select(Block).where(
            Block.conversation_id == topic, Block.kind.is_not("event")
        )
    ).all()
    assert len(rows) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_a_helper_that_counts_the_conversation_is_still_a_reader(self) -> None:
        # `_count` returns how many blocks there are, so a caller pinning that
        # number is pinning the length.
        self._assert_rejected(
            """
def _count(client, conversation_id):
    return len(_history(client, conversation_id))


def test_counts_the_blocks(client, topic):
    assert _count(client, topic) == 2
""",
            kind="length",
            repair="钉死了这段对话里有几个块",
        )

    def test_a_nested_lambda_parameter_still_shadows_the_conversation(self) -> None:
        # The outer lambda's parameter has to stay shadowed inside it, or the
        # inner lambda sees the conversation again.
        findings = self._dump(
            """
def test_sorts_the_numbers(client, topic):
    rows = _history(client, topic)["data"]
    f = lambda rows: (lambda other: rows[0])(1)
    assert f([1, 2]) == 1
"""
        )
        self.assertEqual(findings, [])

    def test_a_query_run_through_execute_is_still_the_conversation(self) -> None:
        # `session.execute(q).scalars()` is the same rows as `session.scalars(q)`.
        self._assert_rejected(
            """
from sqlalchemy import select
from backend.models import Block


def test_counts_the_blocks(session, topic):
    rows = session.execute(
        select(Block).where(Block.conversation_id == topic)
    ).scalars().all()
    assert len(rows) == 2
""",
            kind="length",
            repair="钉死了这段对话里有几个块",
        )

    def test_a_helper_inside_a_test_class_is_a_reader(self) -> None:
        # The definition-order fix has to reach helpers written inside a test
        # class, not only the module-level ones.
        self._assert_rejected(
            """
class TestChat:
    def test_takes_the_first_block(self, client, topic):
        def _fetch():
            return client.get(f"/topics/{topic}/history").json()["data"]

        def _read():
            return _fetch()

        rows = _read()
        assert rows[0]["id"]
""",
            kind="index",
            repair="按位置取块",
        )

    def test_a_lambda_parameter_shadows_the_conversation(self) -> None:
        # `lambda rows: …` takes its own `rows`; the outer read is not it.
        findings = self._dump(
            """
def test_sorts_the_numbers(client, topic):
    rows = _history(client, topic)["data"]
    assert (lambda rows: rows[0])([1, 2]) == 1
"""
        )
        self.assertEqual(findings, [])

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
