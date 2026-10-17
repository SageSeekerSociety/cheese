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
