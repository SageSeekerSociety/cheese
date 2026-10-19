"""The patrol sweep's quiet thresholds, judged by task phrase.

`orchestrator_sweep.py` is ours, not the platform's: it reads the platform and
GitHub on behalf of the dispatcher and ships in no product. Its tests belong
here with the other helper tests in this tree, where `repo-guards` collects
them by the same glob it uses for every helper — not under `backend/tests`,
which collects the product's suites.

The rule under test: a task the platform calls `started`, `bounced` or
`checks_failed` is judged at the short patrol threshold (`--stopped-min`),
because the platform is saying the line has stopped and only a seat that is not
moving looks like that. Every other phrase is judged at the regular quiet
threshold (`--silent-min`). These are the sentences a reader of the tool's
usage could state, and they are what the assertions below pin — not the shape
of the code that decides them.

    python3 .claude/scripts/test_orchestrator_sweep.py
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("orchestrator_sweep.py")
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("orchestrator_sweep", SCRIPT)
sweep = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = sweep
spec.loader.exec_module(sweep)


def _rows(phrase, event_meta, event_minutes=5):
    """The sweep's rows for one task whose phrase and last event are given.

    `get` is the module's one read against the platform, so stubbing it is the
    whole of the network: the sweep sees a task that started 300 minutes ago,
    its seat's last message 60 minutes ago, and its last event block
    `event_minutes` ago. The other three paths answer empty.
    """
    now = datetime.now(UTC)

    def at(minutes):
        return (now - timedelta(minutes=minutes)).isoformat()

    task = {
        "id": "task",
        "number": 1,
        "title": "巡检",
        "agent_handle": "seat",
        "started_at": at(300),
        "presentation": {"phrase": phrase},
    }
    answers = {
        "/tasks?": {"data": [task]},
        "kind=message": {
            "data": [{"created_at": at(60), "author": "seat", "content": "工作中"}]
        },
        "author=system": {"data": []},
        "author=accept": {"data": []},
        "kind=event": {"data": [{"created_at": at(event_minutes), "meta": event_meta}]},
    }

    def get(_token, path):
        return next(answer for key, answer in answers.items() if key in path)

    with patch.object(sweep, "get", get):
        return sweep.sweep("token", "room", 40, set(), False)[0]


class SweepQuietThresholdTest(unittest.TestCase):
    def test_stopped_task_has_short_patrol_threshold(self):
        rows = _rows("started", {"tool": "Bash", "output_bytes": 10})

        self.assertEqual([row["kind"] for row in rows], ["长时间无动静"])
        self.assertEqual(rows[0]["waited_min"], 5)

    def test_delivering_task_uses_regular_quiet_threshold(self):
        event_meta = {"tool": "Bash", "output_bytes": 10}
        rows = _rows("awaiting_checks", event_meta)

        self.assertEqual(rows, [])
        rows = _rows("awaiting_checks", event_meta, event_minutes=40)

        self.assertEqual([row["kind"] for row in rows], ["长时间无动静"])
        self.assertEqual(rows[0]["waited_min"], 40)

    def test_owner_action_stopped_tasks_use_short_patrol_threshold(self):
        event_meta = {"tool": "Bash", "output_bytes": 10}

        for phrase in ("bounced", "checks_failed"):
            with self.subTest(phrase=phrase):
                rows = _rows(phrase, event_meta)

                self.assertEqual([row["kind"] for row in rows], ["长时间无动静"])
                self.assertEqual(rows[0]["waited_min"], 5)

    def test_running_task_ignores_short_patrol_threshold(self):
        rows = _rows("running", {"tool": "Bash", "output_bytes": 10})

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
