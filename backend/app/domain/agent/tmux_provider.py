"""TmuxHooksProvider — the interactive/tmux compute backend (spec §9.1).

Instead of driving the Claude Agent SDK over stream-json (LocalDockerProvider),
this provider runs an INTERACTIVE `claude` inside a long-lived tmux session in
the topic's container, drives it with `tmux send-keys`, and receives structured
events back through Claude Code HTTP hooks (POST /sandbox/hooks/{topic_id}). The
spike (docs/tmux-backend-spike.md) proved hooks emit the same structured events
interactively, mapping 1:1 to AgentEvent.

Per turn (run_turn):
  1. ensure the topic's container exists + is running (image/mount checks),
  2. ensure the `cheese` tmux session exists (lazy; first-launch gates are
     pre-accepted in the image so it reaches the input prompt on its own),
  3. ready handshake: wait for the pane to show the `❯` input box,
  4. inject the prompt (load-buffer + paste-buffer + a separate Enter),
  5. drain the topic's hook queue, translating each hook to an AgentEvent,
  6. on the Stop hook (→ AgentResult) end the turn stream and clean up.

The tmux session is per topic and REUSED across turns, so the conversation stays
continuous inside it (no --resume needed — the session IS the continuity).
"""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

from app.core.config import settings
from app.domain.agent import clone
from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import (
    HooksTurnProvider,
    ScreenSetupError,
    hooks_settings,
)
from app.domain.workspace import service as ws

_CHEESE_AUTHOR = "cheese"
_SESSION = "cheese"  # tmux session name inside the container
_TTYD_PORT = 7681  # in-container ttyd port (published for 施工现场; not wired yet)
_APP_PORT = ws.APP_PORT  # conventional app port (运行环境预览)
# Wait this long for the pane to reach the `❯` input box after (re)starting.
_READY_TIMEOUT_S = 45.0
_READY_POLL_S = 0.4

# The `cheese` CLI lives next to the shim; mounted read-only like the SDK path.
_CHEESE_CLI = Path(settings.sandbox_shim).resolve().parent / "cheese"


def _resume_ready(session_dir: str, resume_session_id: str) -> bool:
    """True when a resumable transcript for ``resume_session_id`` is present in
    this topic's ~/.claude mount (i.e. a cloned/forked conversation was written
    there). Pure so it can be unit-tested without a container. Guards the
    `--resume` path so an ordinary fresh topic (no transcript) never resumes."""
    return clone.transcript_file(Path(session_dir), resume_session_id).is_file()


def pane_ready(capture: str) -> bool:
    """True when a captured tmux pane shows Claude Code's input box (the `❯`
    prompt) — the ready signal before injecting a prompt (spike 就绪握手). Pure so
    it can be unit-tested without a container."""
    return "❯" in capture


def _tmux_container_name(topic_id: uuid.UUID) -> str:
    """Distinct from the SDK container so the two backends never collide. Source of
    truth is ws.tmux_container_name, so the accept/archive reaper frees the same box."""
    return ws.tmux_container_name(topic_id)


def ttyd_endpoint(topic_id: uuid.UUID) -> str | None:
    """`127.0.0.1:<host-port>` of the topic's tmux container ttyd (the read-only
    terminal mirror on the in-container `_TTYD_PORT`), or None when the container
    is down / the port isn't published (old container). Same `docker port` parse
    as workspace.app_preview_url, just for 7681 instead of the app port — used by
    the 施工现场 terminal proxy to reach the container's live pane."""
    if not ws.sandbox_available():
        return None
    result = subprocess.run(
        ["docker", "port", _tmux_container_name(topic_id), str(_TTYD_PORT)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # e.g. "127.0.0.1:55011" (possibly one line per address family).
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    port = line.rsplit(":", 1)[-1]
    return f"127.0.0.1:{port}" if port.isdigit() else None


def _hook_base() -> str:
    """Backend base URL reachable from the container, WITHOUT the /api suffix
    (the hook endpoint is /sandbox/hooks, outside /api)."""
    base = settings.sandbox_api_base.rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base


def _subscription_args() -> list[str]:
    """Docker args that route this sandbox's model calls through the meter.

    The capture is by NAME, not by proxy env: Claude Code issues the model call
    through Node's built-in undici, which ignores HTTPS_PROXY (measured — the
    proxy saw every auxiliary request and never a single /v1/messages, while the
    turns kept answering). Resolving api.anthropic.com to the meter catches
    undici too, because that path still goes through DNS.

    The CA is mounted read-only; the credentials are NOT mounted here — they are
    copied into the session dir per sandbox, because Claude Code rewrites that
    file on every refresh and a shared one would be rewritten under a concurrent
    sandbox's feet (and a failed refresh writes it back empty, which permanently
    kills the subscription).
    """
    if not settings.subscription_enabled:
        return []
    args = ["--add-host", f"api.anthropic.com:{settings.subscription_proxy_host}"]
    ca = settings.subscription_ca_host_path.strip()
    if ca:
        args += ["-v", f"{ca}:/etc/cheese/proxy-ca.pem:ro"]
    return args


async def _docker(*args: str, stdin: bytes | None = None) -> tuple[int, str, str]:
    """Run a docker command off the event loop. Returns (rc, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=stdin)
    return (
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


class TmuxHooksProvider(HooksTurnProvider[str]):
    """The LOCAL hooks backend: runs interactive `claude` in a per-topic tmux
    session inside a platform container, streaming AgentEvents from Claude Code
    hooks. Transport = docker/tmux; the shared turn flow lives in the base
    (HooksTurnProvider) — this class implements only the transport seam. The
    screen ctx is the container name (str)."""

    # (see _subscription_args below for how a subscription turn is captured)

    name = "tmux-hooks"
    _needs_topic_message = "tmux 后端需要 Docker 和话题上下文（缺一不可）"
    _timeout_message = "tmux 轮次超时"

    def __init__(
        self,
        *,
        image: str,
        router: HookRouter | None = None,
        turn_timeout_s: float = 900.0,
    ) -> None:
        super().__init__(router=router, turn_timeout_s=turn_timeout_s)
        self._image = image

    def available(self) -> bool:
        return ws.sandbox_available()

    # --- container / session lifecycle -------------------------------------

    async def _ensure_container(self, topic_id: uuid.UUID, env: dict[str, str]) -> str:
        """Create (or reuse) the topic's tmux container and return its name. Env
        is fixed at creation and reused across turns (the container is long-lived
        per topic — same trade-off as the SDK shim)."""
        name = _tmux_container_name(topic_id)
        rc, cur_image, _ = await _docker("inspect", "-f", "{{.Config.Image}}", name)
        exists = rc == 0
        if exists and cur_image.strip() != self._image:
            await _docker("rm", "-f", name)  # env image changed → rebuild box
            exists = False
        if not exists:
            await self._create_container(name, env)
            return name
        rc, running, _ = await _docker("inspect", "-f", "{{.State.Running}}", name)
        if running.strip() != "true":
            await _docker("start", name)
        return name

    async def _create_container(self, name: str, env: dict[str, str]) -> None:
        # SBX_WORKTREE / SBX_SESSION are mount sources, not container env vars.
        mounts = {"SBX_WORKTREE", "SBX_SESSION"}
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--user",
            "node",
            "-p",
            f"127.0.0.1:0:{_APP_PORT}",
            "-p",
            f"127.0.0.1:0:{_TTYD_PORT}",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{env['SBX_SESSION']}:/home/node/.claude",
            *_subscription_args(),
            "-v",
            f"{env['SBX_WORKTREE']}:/work",
            "-w",
            "/work",
        ]
        if _CHEESE_CLI.is_file():
            args += ["-v", f"{_CHEESE_CLI}:/usr/local/bin/cheese:ro"]
        args += [
            "--network",
            "bridge",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            "512",
            "--label",
            "cheesex-sandbox=1",
            "--label",
            "cheesex-tmux=1",
        ]
        for key, value in env.items():
            if key in mounts:
                continue
            args += ["-e", f"{key}={value}"]
        args += [self._image, "sleep", "infinity"]
        rc, _, err = await _docker(*args)
        if rc != 0:
            raise RuntimeError(f"tmux container create failed: {err.strip()}")

    async def _ensure_session(
        self,
        name: str,
        model: str | None,
        *,
        resume_session_id: str | None = None,
        session_dir: str | None = None,
    ) -> None:
        """Ensure the interactive `claude` tmux session exists (lazy, reused).

        Normally the tmux session IS the continuity, so this starts a FRESH
        `claude`. The ONE exception (enabling clone, fusion-design §6): when a
        resumable session id is given AND its transcript is actually present in
        this topic's ~/.claude mount, start `claude --resume <id>` so a cloned
        (transcript-fork) conversation is picked up on the target topic's first
        turn. The transcript-existence guard keeps every normal path unchanged —
        a fresh topic has no transcript, so it never accidentally resumes."""
        rc, _, _ = await _docker("exec", name, "tmux", "has-session", "-t", _SESSION)
        if rc == 0:
            return
        claude_cmd = "claude --dangerously-skip-permissions"
        if (
            resume_session_id
            and session_dir
            and _resume_ready(session_dir, resume_session_id)
        ):
            claude_cmd += f" --resume {resume_session_id}"
        if model:
            claude_cmd += f" --model {model}"
        rc, _, err = await _docker(
            "exec", name, "tmux", "new-session", "-d", "-s", _SESSION, claude_cmd
        )
        if rc != 0:
            raise RuntimeError(f"tmux new-session failed: {err.strip()}")
        # ttyd (施工现场; not wired to the frontend yet): a read-only terminal
        # mirror. Best-effort — the turn does not depend on it.
        await _docker(
            "exec",
            "-d",
            name,
            "sh",
            "-lc",
            f"pgrep -x ttyd >/dev/null 2>&1 || "
            f"ttyd -R -p {_TTYD_PORT} tmux attach -t {_SESSION}",
        )

    async def _wait_ready(self, name: str) -> bool:
        """Poll capture-pane until the `❯` input box appears (spike 就绪握手)."""
        deadline = asyncio.get_event_loop().time() + _READY_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            rc, out, _ = await _docker(
                "exec", name, "tmux", "capture-pane", "-p", "-t", _SESSION
            )
            if rc == 0 and pane_ready(out):
                return True
            await asyncio.sleep(_READY_POLL_S)
        return False

    async def _send_prompt(self, name: str, prompt: str) -> None:
        """Inject the prompt as one atomic paste, then a SEPARATE Enter (spike:
        bracketed paste + independent Enter, so the prompt isn't split). Wrapped
        so a docker-exec OS failure surfaces as a clean error result (review
        finding — the old code had the send inside the same setup wrap)."""
        try:
            await _docker(
                "exec", "-i", name, "tmux", "load-buffer", "-", stdin=prompt.encode()
            )
            await _docker(
                "exec", name, "tmux", "paste-buffer", "-t", _SESSION, "-d", "-p"
            )
            await _docker("exec", name, "tmux", "send-keys", "-t", _SESSION, "Enter")
        except Exception as exc:  # noqa: BLE001 — a failed send ends the turn
            raise ScreenSetupError(f"tmux 后端启动失败：{exc}") from exc

    # --- turn --------------------------------------------------------------

    def _session_env(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        session_dir: str,
        worktree: str,
        token: str,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
    ) -> dict[str, str]:
        """Container env: model gateway (ANTHROPIC_*) + cheese CLI wiring. Mirrors
        LocalDockerProvider._sandbox_config; SBX_WORKTREE/SBX_SESSION ride along as
        the /work and ~/.claude mount sources (stripped before -e)."""
        merged = {**settings.agent_env(), **(env or {})}
        merged.update(
            {
                "HOME": "/home/node",
                "CHEESE_APP_PORT": str(_APP_PORT),
                "SBX_WORKTREE": worktree,
                "SBX_SESSION": session_dir,
                "CHEESE_API": settings.sandbox_api_base,
                "CHEESE_PROJECT": str(project_id),
                "CHEESE_TOPIC": str(topic_id),
                "CHEESE_AUTHOR": _CHEESE_AUTHOR,
                "CHEESE_TOKEN": token,
                # Where the baked cheese-hook script forwards hook payloads.
                "CHEESE_HOOK_URL": f"{_hook_base()}/sandbox/hooks/{topic_id}",
                # Durable event WAL the forwarder appends to BEFORE its curl, so
                # 现场 events survive a backend restart mid-turn; the backend
                # reconciles it via ws.spool_dir. Inside the ~/.claude session mount
                # (→ host session_dir/cheese-spool), so the backend can read it.
                "CHEESE_HOOK_SPOOL": "/home/node/.claude/cheese-spool",
            }
        )
        if memory_scope:
            merged["CHEESE_MEMORY_SCOPE"] = memory_scope
        if owner:
            merged["CHEESE_OWNER"] = owner
        if turn_id:
            merged["CHEESE_TURN"] = str(turn_id)
        return merged

    async def _precheck(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> object:
        """Fail fast when Docker is absent — BEFORE the base claims the topic's
        hook queue (pre-refactor ordering, review finding). ``topic_id`` is unused
        here (the local backend has no per-topic device affinity)."""
        if not self.available():
            raise ScreenSetupError(self._needs_topic_message)
        return None

    async def _ensure_ready(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        model: str | None,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
        resume_session_id: str | None,
        precheck: object,
    ) -> str:
        """Bring up (or reuse) the topic's tmux `claude` and wait for the `❯`
        input box; return the container name (the screen ctx). Raises
        ScreenSetupError on setup failure / not-ready."""
        # session_dir() seeds the cheese skill + returns the ~/.claude mount path.
        try:
            session_dir = str(ws.session_dir(project_id, topic_id))
            worktree = str(ws.topic_worktree(project_id, topic_id))
            session_env = self._session_env(
                project_id=project_id,
                topic_id=topic_id,
                session_dir=session_dir,
                worktree=worktree,
                token=token,
                env=env,
                memory_scope=memory_scope,
                owner=owner,
                turn_id=turn_id,
            )
            name = await self._ensure_container(topic_id, session_env)
            # Seed hooks + skip-disclaimer settings before the session starts
            # (only read at session creation), then bring the session up.
            self._write_session_settings(session_dir)
            await self._ensure_session(
                name,
                model,
                resume_session_id=resume_session_id,
                session_dir=session_dir,
            )
            # _wait_ready inside the wrap too: its docker exec can itself fail
            # (docker binary vanishing mid-turn) — that must surface as a clean
            # error result, not a raw exception (review finding).
            ready = await self._wait_ready(name)
        except Exception as exc:  # noqa: BLE001 — any setup failure ends the turn
            raise ScreenSetupError(f"tmux 后端启动失败：{exc}") from exc
        if not ready:
            raise ScreenSetupError("tmux 会话未就绪（未等到输入框），已放弃本轮")
        return name

    def _write_session_settings(self, session_dir: str) -> None:
        """Write ~/.claude/settings.json (hooks + skip-disclaimer) into the
        container's session mount. Idempotent — the hook command is static (the
        per-topic URL + token live in the container env, not the file)."""
        target = Path(session_dir) / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(hooks_settings(), ensure_ascii=False),
            encoding="utf-8",
        )
        target.chmod(0o666)

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Snapshot the interactive session's native edits into version history
        (same contract as LocalDockerProvider). Best-effort — never fail a turn."""
        if not self.available():
            return
        try:
            ws.snapshot_worktree(project_id, topic_id)
        except Exception:  # noqa: BLE001 — git snapshot is best-effort
            pass
