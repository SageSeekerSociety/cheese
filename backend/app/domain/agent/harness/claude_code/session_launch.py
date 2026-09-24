"""What a Claude Code session is started with: its settings, and the launch plan.

``ClaudeLaunch`` is what a channel is handed, and all it can do with one is say
where its machine keeps things and take back a launch; the answer being Claude
Code's is not something the transport finds out.

The settings file and the system prompt file are each read ONCE, at launch: a
session that is merely reused keeps what it started with. Both are written on
every launch anyway, because the write is for the NEXT fresh session.
"""

from dataclasses import dataclass

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.cli import DISALLOWED_TOOLS
from app.domain.agent.harness.launch import ExecutorLaunch, MachineLaunch, MachinePlace


def session_settings() -> dict:
    """``$CLAUDE_CONFIG_DIR/settings.json`` for a room's session.

    No hook here observes the session: the runner reads what it does from its
    own stdout. The hooks left are the ones that act — the model catalogue
    written as agent definitions at session start and on each prompt — and the
    remote-execution client adds the rest of its own (``client.prepare``).
    """
    # 发现层：把项目的模型目录写成本会话的 CC 分身定义文件（名字、一句话描
    # 述、model=目录 id），主 agent 于是在 Agent 工具的可用清单里直接读到可
    # 指定哪个模型。闸在准入（/llm/admission）——指定了目录外的模型会被拒并
    # 列出可选；这里是让人事先知道范围。sync-agents 自己恒退出 0，够不着后
    # 端时这一轮一切照旧。
    sync_agents = [
        {
            "hooks": [
                {
                    "type": "command",
                    # Existing rooms can retain a CLI release without sync-agents.
                    # Discovery must never block a user prompt on that old CLI.
                    "command": "cheese sync-agents || true",
                    "timeout": 15,
                }
            ]
        }
    ]
    return {
        "skipDangerousModePermissionPrompt": True,
        # Stream liveness is separate from WebFetch's response-error handling.
        "env": {
            "CLAUDE_ENABLE_STREAM_WATCHDOG": "1",
            # The watchdog aborts a stream that has produced no BYTES for its
            # idle window, and left alone that window is SHORTER than this
            # deployment's own silence handling: the CLI uses 180 s whenever it
            # believes it is on the first-party API, which is exactly what an
            # unset `ANTHROPIC_BASE_URL` means (provider_env.py leaves it unset
            # so the metering proxy stays transparent). Nothing on the path
            # writes a keepalive byte either — LiteLLM holds the first chunk
            # until TTFT and mitmproxy stays silent while the provider thinks —
            # so a long thinking window is indistinguishable from a dead
            # connection, and the CLI kills the turn mid-stream before the
            # platform's own gates ever look at it.
            #
            # Setting this variable at all is what leaves that 180 s branch (the
            # CLI floors it at 300 s and never honours anything lower), so the
            # number here is a ceiling for the CLI, not a second opinion on how
            # long a turn may run.
            "CLAUDE_STREAM_IDLE_TIMEOUT_MS": "900000",
        },
        # Previews belong in Cheese, not on claude.ai via the Artifact tool.
        "enableArtifact": False,
        # The same refusal the argv carries (`cli.LAUNCH_ARGS`), stated where a
        # settings reader looks for it.
        "permissions": {"deny": list(DISALLOWED_TOOLS)},
        # Set before the first turn: changing this later cannot remove a URL
        # already present in the conversation's model-visible history.
        "attribution": {"sessionUrl": False},
        "hooks": {
            "SessionStart": sync_agents,
            "UserPromptSubmit": sync_agents,
        },
    }


@dataclass(frozen=True, slots=True)
class ClaudeLaunch:
    """Claude Code as a ``LaunchPlan``: 跑什么，交给机器去说在哪。

    The values a turn actually chooses, held until a channel says where its
    machine keeps things — at which point ``on`` turns them into the launch.
    """

    system_prompt: str
    model: str | None = None
    resume_session_id: str | None = None
    harness: str = CLAUDE_CODE

    @property
    def execution(self) -> ExecutorLaunch:
        from app.domain.agent.harness.claude_code.remote_execution import launch

        return launch

    def on(self, place: MachinePlace) -> MachineLaunch:
        from app.domain.agent.harness.claude_code.device_launch import on_machine

        return on_machine(
            place,
            system_prompt=self.system_prompt,
            # A screen is retired and reopened for reasons that say nothing
            # about the conversation, so every launch offers to continue it.
            resume_session_id=self.resume_session_id,
        )
