import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".claude"
    / "scripts"
    / "orchestrator_sweep.py"
)
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("orchestrator_sweep", SCRIPT)
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def task_rows(monkeypatch, phrase, event_meta):
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
        "kind=event": {"data": [{"created_at": at(5), "meta": event_meta}]},
    }
    monkeypatch.setattr(
        sweep,
        "get",
        lambda _token, path: next(
            answer for key, answer in answers.items() if key in path
        ),
    )
    return sweep.sweep("token", "room", 40, set(), False)[0]


def test_stopped_task_has_short_patrol_threshold(monkeypatch):
    rows = task_rows(monkeypatch, "started", {"tool": "Bash", "output_bytes": 10})

    assert [row["kind"] for row in rows] == ["长时间无动静"]
    assert rows[0]["waited_min"] == 5


def test_delivering_task_uses_regular_quiet_threshold(monkeypatch):
    event_meta = {"tool": "Bash", "output_bytes": 10}
    rows = task_rows(monkeypatch, "awaiting_checks", event_meta)

    assert rows == []


def test_running_task_ignores_short_patrol_threshold(monkeypatch):
    rows = task_rows(monkeypatch, "running", {"tool": "Bash", "output_bytes": 10})

    assert rows == []
