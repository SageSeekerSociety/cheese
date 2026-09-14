"""The platform's system prompt must actually reach the `claude` a turn runs.

For 1600+ production turns it never did: the hooks base accepted
``system_prompt`` and dropped it, so 芝士 ran as a stock `claude` — no identity,
no knowledge of the product it lives in. These tests pin the delivery contract
at the process boundary: the LAUNCH LINE must carry
``--append-system-prompt-file`` and the file it points at must hold the exact
text.
"""

import subprocess
import uuid

import pytest

from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import device_launch
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch

_FLAG = "--append-system-prompt-file"
_PROMPT = "你是芝士，一个 cheese 平台上的正式成员。\n平台事件通过 cheese CLI 到达。\n"


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

    async def open_screen(self, device_id, command, **kw) -> HubScreen:
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

    def update_screen(self, sid: str, **values) -> HubScreen:
        screen = next(screen for screen in self.opened if screen.sid == sid)
        for name, value in values.items():
            setattr(screen, name, value)
        return screen


@pytest.mark.anyio
async def test_device_screen_opens_with_the_system_prompt():
    hub = _RecordingHub()
    provider = DeviceChannel(hub=hub, public_base="http://cheese.test")  # type: ignore[arg-type]

    await provider._ensure_screen(
        device_id="dev1",
        agent_user_id=1,
        agent_handle="cheese",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        token="tok",
        env=None,
        launch=ClaudeLaunch(system_prompt=_PROMPT),
    )

    # The launcher rides to the device as a FILE over `exec` (tmux argv caps out
    # around 16KB and a real system prompt exceeds it); the screen command is a
    # short runner pointing at that file. The prompt lives in the shipped script.
    script = hub.shipped[0]
    assert _heredoc_body(script) == _PROMPT
    assert _FLAG in script
    assert "/.cheese/launch/" in hub.opened[0].command[2]
