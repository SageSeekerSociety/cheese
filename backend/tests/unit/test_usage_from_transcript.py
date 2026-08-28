"""Carrying a turn's real token counts off the machine.

The numbers exist in exactly one place — Claude Code's transcript on the host —
and that file dies with the machine. That is why a week of spend could not be
attributed to a project, a topic, or even a prompt: the platform's own usage
table held four rows across two weeks, all zero, while real money went.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.domain.agent.harness.claude_code.device_launch import CHEESE_USAGE_READER
from app.domain.agent.harness.claude_code.hook_events import usage_from_hook
from app.domain.agent.service import AgentUsage

_TRANSCRIPT = [
    {
        "type": "assistant",
        "message": {
            "model": "glm-5.2",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 5000,
            },
        },
    },
    {
        "type": "assistant",
        "message": {
            "model": "glm-5.2",
            "usage": {
                "input_tokens": 41,
                "output_tokens": 8,
                "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 300,
            },
        },
    },
    {"type": "user", "message": {"content": "a turn with no usage block"}},
]


def _read_transcript(lines: list) -> dict | None:
    """Run the reader the machine actually runs, against a real transcript."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.jsonl"
        t.write_text("".join(json.dumps(x) + "\n" for x in lines))
        prog = Path(tmp) / "reader.py"
        prog.write_text(CHEESE_USAGE_READER)
        out = subprocess.run(
            ["python3", str(prog)],
            input=json.dumps({"transcript_path": str(t)}),
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ},
        )
        return json.loads(out.stdout) if out.stdout.strip() else None


def test_the_reader_totals_a_real_transcript():
    got = _read_transcript(_TRANSCRIPT)

    assert got == {
        "hook_event_name": "CheeseUsage",
        "model": "glm-5.2",
        "input": 141,
        "output": 28,
        "cache_read": 5900,
        "cache_write": 300,
    }, got


def test_a_transcript_with_nothing_to_report_stays_silent():
    """An empty report would create a zero row that looks like a free turn —
    the exact shape that made the usage table useless before."""
    assert _read_transcript([{"type": "user", "message": {"content": "hi"}}]) is None


def test_cache_reads_are_counted_not_dropped():
    """One document-writing task read 2.9M cached tokens against 141k of fresh
    input — 20x, and half its cost. Dropping them under-reports a turn by more
    than it reports."""
    usage = usage_from_hook(
        {
            "hook_event_name": "CheeseUsage",
            "model": "glm-5.2",
            "input": 141,
            "output": 28,
            "cache_read": 5900,
            "cache_write": 300,
        }
    )

    assert isinstance(usage, AgentUsage)
    assert usage.input_tokens == 141 + 5900 + 300, "cache was dropped"
    assert usage.output_tokens == 28
    assert usage.model == "glm-5.2"
