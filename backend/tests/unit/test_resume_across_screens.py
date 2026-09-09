"""重开一块屏幕，接着上一段对话说 —— 而不是从零开始。

A screen is retired and reopened for reasons that have nothing to do with what
was said on it: the credential it was launched with expired, its `claude` died,
the machine-local tunnel helper went away. The conversation itself is not gone —
Claude Code wrote it to a transcript on that machine's disk, and it is still
there. Until the launch started asking for it, every one of those gates reset
the topic's agent to a blank slate.

The decision cannot be made on this side. `--resume <id>` whose transcript is
absent does not degrade to a fresh session — claude exits and the pane never
draws an input box — so it has to be guarded by looking at the file, and the
file is on a machine behind NAT that the backend cannot stat. So the launcher
carries the guard, and these tests drive the REAL generated shell (with a stub
tmux) to check what it decides, plus one turn through the real channel to check
the pointer reaches it at all.
"""

import asyncio
import os
import subprocess
import time
import uuid
from types import SimpleNamespace

from app.domain.agent.harness.claude_code import device_launch
from app.domain.agent.harness.claude_code.hook_events import HookRouter
from tests.unit.test_device_launch import STUB_SOCK, _stub_tmux_env
from tests.unit.test_device_provider import FakeHub, _provider

SESSION_ID = "9f1c0d3e-2b4a-4c6e-8d10-7a5b3c9e1f20"


def _resume_deciding_block() -> str:
    """The launcher from where it builds `$CLAUDE` through the tmux-hosting
    branch, standalone — the span in which the resume decision is made and the
    command it produces is either used (a fresh session) or ignored (an adopted
    one). Its env is supplied by the caller instead of the launcher's earlier
    setup, exactly as in test_device_launch's tmux tests."""
    script = device_launch.build_launch_script()
    start = script.index('CLAUDE="\\"$CLAUDE_BIN\\"')
    tail = '  exec tmux -S "$CHEESE_TMUX_SOCK" attach -t "$SESSION"\n'
    body = script[start:].split(tail, 1)[0]
    return "set -e\n" + body + tail + "fi\n"


def _machine(tmp_path, *, transcript_for: str | None = SESSION_ID):
    """A device home with a stub tmux, and (by default) a transcript of an
    earlier conversation sitting where Claude Code left it."""
    home, env, log = _stub_tmux_env(tmp_path)
    config_dir = home / ".claude"
    if transcript_for is not None:
        slug = config_dir / "projects" / "-home-agent-work"
        slug.mkdir(parents=True)
        (slug / f"{transcript_for}.jsonl").write_text('{"type":"user"}\n')
    env = {
        **env,
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_BIN": "/usr/bin/claude",
        "CLAUDE_MODEL": "",
        "TMUX": f"{STUB_SOCK},1,0",
    }
    env.pop("CLAUDE", None)  # this block builds it; it must not be inherited
    return home, env, log


def _launch(env, *, resume: str | None = SESSION_ID, panes: str = "12345"):
    """Run the launcher's decision span once, as a turn would."""
    full = {
        **env,
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 100_000),
        "STUB_PANES": panes,
    }
    if resume is not None:
        full["CHEESE_RESUME_SESSION"] = resume
    proc = subprocess.run(
        ["sh", "-c", _resume_deciding_block()],
        env=full,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def _launched_commands(env) -> list[str]:
    """Every `claude` command line the stub tmux was asked to host."""
    path = env["STUB_ARGS"]
    if not os.path.exists(path):
        return []
    # The stub logs every new-session argument on its own line; the one that
    # hosts a claude is the session command, and only it carries the flags.
    return [
        line
        for line in open(path).read().splitlines()
        if "--dangerously-skip-permissions" in line
    ]


def test_a_reopened_screen_picks_the_conversation_back_up(tmp_path):
    """The whole point: the transcript of this topic's conversation is on this
    machine, so the fresh `claude` is told to continue it."""
    _home, env, _log = _machine(tmp_path)

    _launch(env)

    hosted = _launched_commands(env)
    assert len(hosted) == 1, hosted
    assert f"--resume {SESSION_ID}" in hosted[0], (
        "a screen reopened over a transcript that is right there started from "
        f"nothing: {hosted[0]}"
    )


def test_a_conversation_the_machine_no_longer_has_starts_clean(tmp_path):
    """The boundary that decides whether this feature is safe. A machine whose
    disk was wiped (or a topic that moved to a different machine) has no
    transcript to continue — and `--resume` there would exit instead of
    starting. The launch must fall back to a NEW conversation, never to a
    screen that fails to come up."""
    _home, env, _log = _machine(tmp_path, transcript_for=None)

    _launch(env)

    hosted = _launched_commands(env)
    assert len(hosted) == 1, "the topic must still get a claude"
    assert "--resume" not in hosted[0], (
        f"asked to continue a conversation this machine does not have: {hosted[0]}"
    )


def test_an_empty_transcript_is_not_a_conversation(tmp_path):
    """A file claude created and never wrote to is the same absence, and reads
    the same way to a glob."""
    home, env, _log = _machine(tmp_path, transcript_for=None)
    slug = home / ".claude" / "projects" / "-home-agent-work"
    slug.mkdir(parents=True)
    (slug / f"{SESSION_ID}.jsonl").write_text("")

    _launch(env)

    assert "--resume" not in _launched_commands(env)[0]


def test_a_live_session_is_adopted_and_never_asked_to_resume(tmp_path):
    """An adopted `claude`已经带着自己的对话在跑. Resuming is only ever about the
    session a launch CREATES, so a reuse must not start a second one."""
    _home, env, log = _machine(tmp_path)

    _launch(env)  # creates
    _launch(env)  # the same screen, still alive

    assert len(_launched_commands(env)) == 1, (
        "a live session was relaunched instead of adopted"
    )
    assert log.read_text().split().count("new") == 1


def test_a_resume_that_killed_the_screen_is_not_tried_twice(tmp_path):
    """The wedge this must not become. A transcript that is present but that
    claude cannot read kills the pane; a dead pane is (correctly) grounds to
    retire the session; and if the relaunch asked for the same transcript again
    the topic would never get a working screen. The second launch starts clean
    instead."""
    _home, env, _log = _machine(tmp_path)

    _launch(env)  # resumes...
    _launch(env, panes="1")  # ...and its pane is dead: retire and relaunch

    hosted = _launched_commands(env)
    assert len(hosted) == 2, hosted
    assert f"--resume {SESSION_ID}" in hosted[0]
    assert "--resume" not in hosted[1], (
        f"the transcript that broke the screen was handed to it again: {hosted[1]}"
    )


def test_a_resume_that_worked_can_be_used_again_later(tmp_path):
    """And the other side of that: a session that came up fine and was adopted
    leaves nothing behind, so the NEXT time the screen is retired the same
    conversation is picked up again. Otherwise one retirement would cost every
    later one its memory."""
    _home, env, _log = _machine(tmp_path)

    _launch(env)  # resumes
    _launch(env)  # adopted — the resume evidently worked
    _launch(env, panes="1")  # much later: the credential died, say

    hosted = _launched_commands(env)
    assert len(hosted) == 2, hosted
    assert f"--resume {SESSION_ID}" in hosted[1], (
        f"a proven-good conversation was dropped on the next reopen: {hosted[1]}"
    )


async def test_a_turn_hands_the_machine_the_conversation_to_continue(monkeypatch):
    """The other half. The platform already works out which conversation this
    place resumes by; that answer has to reach the machine, and the screen env
    is where the launcher reads it. Dropped here, every guard above is dead
    code."""
    from app.domain.agent.device_provider import DeviceChannel

    async def public_base(self, _device_id):
        return self._public_base

    monkeypatch.setattr(DeviceChannel, "_device_api_base", public_base)

    async def active_room(_self, topic_id):
        return SimpleNamespace(id=topic_id, resource_id=None)

    monkeypatch.setattr(
        "app.domain.topic.services.TopicService.lock_for_execution", active_room
    )
    opened = asyncio.Event()

    class RecordingHub(FakeHub):
        async def open_screen(self, *args, **kwargs):
            screen = await super().open_screen(*args, **kwargs)
            opened.set()
            return screen

    hub = RecordingHub()
    runtime = _provider(hub, HookRouter(), uuid.uuid4())

    async def drain():
        async for _event in runtime.run_turn(
            project_id=uuid.uuid4(),
            topic_id=uuid.uuid4(),
            prompt="hi",
            system_prompt="",
            resume_session_id=SESSION_ID,
        ):
            pass

    task = asyncio.create_task(drain())
    try:
        await asyncio.wait_for(opened.wait(), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert hub.envs, "no screen was ever opened"
    assert (hub.envs[0] or {}).get("CHEESE_RESUME_SESSION") == SESSION_ID, (
        "the turn knew which conversation to continue and the machine was never "
        f"told: {hub.envs}"
    )
