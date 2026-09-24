"""重开一块屏幕，接着上一段对话说 —— 而不是从零开始。

A screen is retired and reopened for reasons that have nothing to do with what
was said on it: the credential it was launched with expired, its `claude` died,
the machine-local tunnel helper went away. The conversation itself is not gone —
Claude Code wrote it to a transcript on that machine's disk, and it is still
there.

The decision cannot be made on the backend's side: `--resume <id>` whose
transcript is absent does not degrade to a fresh session, it exits, and the file
is on a machine the backend cannot stat. So the runner makes it when it starts
the session, and these tests drive the real runner with a stand-in `claude` that
records how it was started.
"""

import asyncio
import json
import shlex
import sys
import uuid

import pytest

from app.domain.agent.harness.claude_code.runner import Runner

pytestmark = pytest.mark.anyio

SESSION_ID = "9f1c0d3e-2b4a-4c6e-8d10-7a5b3c9e1f20"

# Records its argv, then either dies before answering (a transcript it cannot
# continue) or reports `init` and holds the session until stdin closes.
STAND_IN = """\
import json, os, sys
with open(os.environ["STAND_IN_ARGS"], "a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ["STAND_IN_MODE"] == "fail":
    sys.exit(1)
print(json.dumps({"type": "system", "subtype": "init", "session_id": "x"}), flush=True)
sys.stdin.read()
"""


@pytest.fixture
def machine(tmp_path):
    config = tmp_path / "home/.claude"
    config.mkdir(parents=True)
    stand_in = tmp_path / "claude.py"
    stand_in.write_text(STAND_IN)
    return tmp_path, config, stand_in


def _transcript(config, session_id=SESSION_ID, content='{"type":"user"}\n'):
    slug = config / "projects/-home-agent-work"
    slug.mkdir(parents=True, exist_ok=True)
    (slug / f"{session_id}.jsonl").write_text(content)


async def _start(machine, *, resume=SESSION_ID, mode="init") -> list[str]:
    """One screen's life: the runner starts the session, and it ends.

    ``init`` sessions are ended by us once they have answered; ``fail`` ones
    exit by themselves before answering. Returns the argv the stand-in got.
    """
    root, config, stand_in = machine
    args = root / "args.jsonl"
    runner = Runner(root / "state")
    await runner.start(
        command=f"{shlex.quote(sys.executable)} {shlex.quote(str(stand_in))}",
        env={
            "PATH": "/usr/bin:/bin",
            "CLAUDE_CONFIG_DIR": str(config),
            "STAND_IN_ARGS": str(args),
            "STAND_IN_MODE": mode,
        },
        resume=resume,
        agent_handle="agent",
    )
    try:
        assert runner.process is not None
        if mode == "fail":
            await asyncio.wait_for(runner.process.wait(), 10)
        else:
            async with asyncio.timeout(10):
                while not runner.proven:
                    await asyncio.sleep(0.02)
    finally:
        await runner.close()
    return json.loads(args.read_text().splitlines()[-1])


def _session_id(argv: list[str]) -> str:
    assert argv[0] == "--session-id", argv
    return str(uuid.UUID(argv[1]))


async def test_a_reopened_screen_picks_the_conversation_back_up(machine):
    """The whole point: the transcript is on this machine, so the new session
    continues it."""
    _transcript(machine[1])

    assert await _start(machine) == ["--resume", SESSION_ID]


async def test_a_conversation_the_machine_no_longer_has_starts_clean(machine):
    """A machine whose disk was wiped, or a topic that moved to another one, has
    no transcript to continue — and `--resume` there would exit instead of
    starting. The session starts a NEW conversation, never a screen that fails
    to come up."""
    argv = await _start(machine)

    assert _session_id(argv) != SESSION_ID


async def test_an_empty_transcript_is_not_a_conversation(machine):
    """A file claude created and never wrote to is the same absence."""
    _transcript(machine[1], content="")

    assert await _start(machine) != ["--resume", SESSION_ID]


async def test_a_resume_that_died_before_answering_is_not_tried_twice(machine):
    """The wedge this must not become. A transcript that is present but that
    claude cannot continue kills the session before it answers; if the next
    screen asked for the same transcript again the topic would never get a
    working session. The second start begins afresh instead."""
    _transcript(machine[1])

    assert await _start(machine, mode="fail") == ["--resume", SESSION_ID]
    assert _session_id(await _start(machine)) != SESSION_ID


async def test_a_resume_that_worked_can_be_used_again_later(machine):
    _transcript(machine[1])

    assert await _start(machine) == ["--resume", SESSION_ID]
    assert await _start(machine) == ["--resume", SESSION_ID]


async def test_the_session_it_started_last_time_is_the_one_it_continues(machine):
    """Nothing offered: the runner's own record of the session it started is the
    candidate, and a fresh session gets the id it will later be resumed by."""
    first = _session_id(await _start(machine, resume=None))
    _transcript(machine[1], session_id=first)

    assert await _start(machine, resume=None) == ["--resume", first]


async def test_a_session_that_never_wrote_keeps_its_id(machine):
    """A session that ended before writing a transcript has nothing to resume,
    and its id is still free: the next start claims it again rather than
    leaving the backend holding an id no session will ever have."""
    first = _session_id(await _start(machine, resume=None))

    assert _session_id(await _start(machine, resume=None)) == first
