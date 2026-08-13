"""The platform's system prompt must actually reach the `claude` a turn runs.

For 1600+ production turns it never did: the hooks base accepted
``system_prompt`` and dropped it, so 芝士 ran as a stock `claude` — no identity,
no knowledge of the product it lives in. These tests pin the delivery contract
at the process boundary for both hooks transports: the LAUNCH LINE must carry
``--append-system-prompt-file`` and the file it points at must hold the exact
text. (The SDK backend passes ``system_prompt`` natively and is not covered
here.)
"""

import subprocess
import uuid

import pytest

from app.domain.agent import device_launch
from app.domain.agent import tmux_provider as tp
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceProvider
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.tmux_provider import TmuxHooksProvider

_FLAG = "--append-system-prompt-file"
_PROMPT = "你是芝士，一个 cheese 平台上的正式成员。\n平台事件通过 cheese CLI 到达。\n"


# --- tmux transport ---------------------------------------------------------


def _fake_docker(calls, *, session_alive: bool):
    async def fake(*args: str, stdin: bytes | None = None):
        calls.append(args)
        if args[:2] == ("inspect", "-f") and "{{.Config.Image}}" in args:
            return 1, "", "no such object"
        if args[:2] == ("inspect", "-f") and "{{.State.Running}}" in args:
            return 0, "true", ""
        if "capture-pane" in args:
            return 0, "❯ ", ""
        if "has-session" in args:
            return (0, "", "") if session_alive else (1, "", "")
        return 0, "", ""

    return fake


def _stub_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(tp.ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(tp.ws, "session_dir", lambda p, t: tmp_path / "session")
    monkeypatch.setattr(tp.ws, "topic_worktree", lambda p, t: tmp_path / "work")
    (tmp_path / "session").mkdir(exist_ok=True)
    (tmp_path / "work").mkdir(exist_ok=True)
    return tmp_path / "session"


async def _one_turn(provider: TmuxHooksProvider, router, topic_id, **kw) -> list:
    """Drive a full run_turn; the fake 'container' answers Stop immediately."""

    async def fake_send(name: str, prompt: str) -> None:
        router.push(
            str(topic_id),
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "ok",
                "session_id": "s",
            },
        )

    provider._send_prompt = fake_send  # type: ignore[method-assign]
    return [
        e
        async for e in provider.run_turn(
            project_id=uuid.uuid4(), topic_id=topic_id, prompt="hi", **kw
        )
    ]


@pytest.mark.anyio
async def test_tmux_launch_carries_the_system_prompt(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tp, "_docker", _fake_docker(calls, session_alive=False))
    session_dir = _stub_workspace(monkeypatch, tmp_path)
    router = HookRouter()
    provider = TmuxHooksProvider(image="img:test", router=router)

    await _one_turn(
        provider, router, uuid.uuid4(), system_prompt=_PROMPT, resume_session_id=None
    )

    launch = " ".join(next(c for c in calls if "new-session" in c))
    assert f"{_FLAG} /home/node/.claude/cheese-system-prompt.md" in launch
    written = (session_dir / "cheese-system-prompt.md").read_text(encoding="utf-8")
    assert written == _PROMPT


@pytest.mark.anyio
async def test_tmux_empty_system_prompt_launches_stock_claude(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tp, "_docker", _fake_docker(calls, session_alive=False))
    _stub_workspace(monkeypatch, tmp_path)
    router = HookRouter()
    provider = TmuxHooksProvider(image="img:test", router=router)

    await _one_turn(
        provider, router, uuid.uuid4(), system_prompt="", resume_session_id=None
    )

    launch = " ".join(next(c for c in calls if "new-session" in c))
    assert _FLAG not in launch


@pytest.mark.anyio
async def test_tmux_reused_session_still_records_the_current_prompt(
    monkeypatch, tmp_path
):
    """A live session keeps the prompt it launched with — but the file must
    track the CURRENT text so the next fresh session (container rebuild, crash)
    starts with it, not with a stale one."""
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tp, "_docker", _fake_docker(calls, session_alive=True))
    session_dir = _stub_workspace(monkeypatch, tmp_path)
    (session_dir / "cheese-system-prompt.md").write_text("旧的", encoding="utf-8")
    router = HookRouter()
    provider = TmuxHooksProvider(image="img:test", router=router)

    await _one_turn(
        provider, router, uuid.uuid4(), system_prompt=_PROMPT, resume_session_id=None
    )

    assert not any("new-session" in c for c in calls)  # reused, not relaunched
    written = (session_dir / "cheese-system-prompt.md").read_text(encoding="utf-8")
    assert written == _PROMPT


# --- device transport -------------------------------------------------------


def _heredoc_body(script: str) -> str:
    return script.split("<<'SYSPROMPT'\n", 1)[1].split("SYSPROMPT\n", 1)[0]


def test_device_launch_embeds_the_system_prompt():
    script = device_launch.build_launch_script(system_prompt=_PROMPT)
    assert _heredoc_body(script) == _PROMPT
    assert f'{_FLAG} \\"$CHEESE_SP\\"' in script


def test_device_launch_keeps_hostile_prompt_text_inert():
    """The prompt is free text going through a shell script: quotes, backticks
    and $() must land in the file verbatim, not execute. The quoted heredoc is
    what guarantees that — and `sh -n` proves the script still parses."""
    hostile = 'a "quote" `tick` $(reboot) $HOME\n'
    script = device_launch.build_launch_script(system_prompt=hostile)
    assert _heredoc_body(script) == hostile
    proc = subprocess.run(["sh", "-n"], input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_device_launch_without_prompt_writes_an_empty_file():
    script = device_launch.build_launch_script()
    assert _heredoc_body(script) == ""
    # The flag is guarded by [ -s ]: an empty file means stock claude.
    assert '[ -s "$CHEESE_SP" ]' in script


class _RecordingHub:
    """Minimal DeviceHub stand-in: records the launch command and the launcher
    script shipped over `exec` (the script travels as a file, never tmux argv)."""

    def __init__(self) -> None:
        self.opened: list[HubScreen] = []
        self.shipped: list[str] = []  # exec stdin payloads (launch scripts)

    def online_device_ids(self) -> list[str]:
        return ["dev1"]

    def is_online(self, device_id: str) -> bool:
        return device_id == "dev1"

    def all_online_screens(self) -> list[HubScreen]:
        return list(self.opened)

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ) -> dict:
        if stdin is not None:
            self.shipped.append(stdin)
        return {"stdout": "", "stderr": "", "exit": 0, "truncated": False}

    async def open_screen(self, device_id, command, source, **kw) -> HubScreen:
        screen = HubScreen(
            sid="s1",
            device_id=device_id,
            command=command,
            token="tok",
            agent_user_id=kw["agent_user_id"],
            agent_handle=kw["agent_handle"],
            project_id=kw["project_id"],
            topic_id=kw["topic_id"],
            hook_key=kw.get("hook_key", ""),
        )
        self.opened.append(screen)
        return screen


@pytest.mark.anyio
async def test_device_screen_opens_with_the_system_prompt(monkeypatch):
    async def co_located(_device_id: str) -> bool:
        return False

    hub = _RecordingHub()
    provider = DeviceProvider(hub=hub, public_base="http://cheese.test")  # type: ignore[arg-type]
    monkeypatch.setattr(provider, "_is_co_located", co_located)

    await provider._ensure_screen(
        device_id="dev1",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        token="tok",
        model=None,
        env=None,
        system_prompt=_PROMPT,
    )

    # The launcher rides to the device as a FILE over `exec` (tmux argv caps out
    # around 16KB and a real system prompt exceeds it); the screen command is a
    # short runner pointing at that file. The prompt lives in the shipped script.
    script = hub.shipped[0]
    assert _heredoc_body(script) == _PROMPT
    assert _FLAG in script
    assert "/.cheese/launch/" in hub.opened[0].command[2]
