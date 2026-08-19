"""TmuxChannel — the interactive/tmux compute channel (spec §9.1).

A long-lived tmux session in the topic's container on this host, driven with
`tmux send-keys` over `docker exec`. What runs in it — an interactive `claude`,
sensed through Claude Code hooks (POST /sandbox/hooks/{topic_id}) — is
``ClaudeCodeRuntime``'s business; this file is Docker, worktrees, port slots and
window sizes. The spike (docs/tmux-backend-spike.md) proved hooks emit the same
structured events interactively, mapping 1:1 to AgentEvent.

Per turn:
  1. ensure the topic's container exists + is running (image/mount checks),
  2. ensure the `cheese` tmux session exists (lazy; first-launch gates are
     pre-accepted in the image so it reaches the input prompt on its own),
  3. ready handshake: wait for the pane to show the `❯` input box,
  4. inject the prompt (load-buffer + paste-buffer + a separate Enter, each
     half confirmed against the screen — see send_prompt),
  5. drain the topic's hook queue, translating each hook to an AgentEvent,
  6. on the Stop hook (→ AgentResult) end the turn stream and clean up.

The tmux session is per topic and REUSED across turns, so the conversation stays
continuous inside it (no --resume needed — the session IS the continuity).

One box per ROOM, not per topic. A room's母话题 and every task split out of it
share a container and hold one tmux session each. This is the necessary half of
"containers are never reaped": kept forever AND one per topic, the box count only
ever climbs and 2GB apiece exhausts memory first; kept forever and one per room,
it tracks the number of rooms, which tracks the number of people. Sharing is also
nearly free here — `ws.sandbox_project_mounts` already gives every topic of a
project the identical mount set (one mount over the whole `.worktrees` tree,
because hardlinks cannot cross bind mounts), so same-project topics were never
isolated from each other in the first place.

Everything that DIFFERS between the topics in a box therefore travels per tmux
session, never in the container environment: the topic id, its hook token, its
config dir, its cwd, its port slot. `tmux new-session -e` writes those before
`claude` execs. Inheritance is not an option and this is measured, not
theoretical — a tmux session inherits from the SERVER's global environment,
frozen when the server first started, so the second topic in a box would run
`claude` on the FIRST topic's identity and report its events as that topic (the
device backend hit exactly this on a shared machine, see device_launch.py).
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token, verify_scoped_token
from app.domain.agent import awaited_tasks, clone, provider_env
from app.domain.agent.harness.claude_code import (
    SESSION_TOKEN_TTL_S,
    ActivityTracker,
    Channel,
    ScreenSetupError,
    drop_screen_subscriptions,
    hooks_settings,
)
from app.domain.agent.sandbox_notices import warn_container_rebuilt
from app.domain.agent.service import CLAUDE_BASE_CMD
from app.domain.agent.tmux_control import TmuxControlClient
from app.domain.identity.handles import topic_agent_handle
from app.domain.workspace import service as ws

_TTYD_PORT = ws.TTYD_PORT  # in-container ttyd base port (施工现场 pane mirror)
# The platform's system prompt travels as a FILE in the ~/.claude session mount
# (host: session_dir/cheese-system-prompt.md), not inline on the command line:
# it is multi-KB free text, and the tmux launch string goes through sh -c.
# There is deliberately no fixed absolute path for it any more: it lives in the
# topic's OWN config dir, which differs per session inside a shared box, so
# `_ensure_session` builds the path from that session's CLAUDE_CONFIG_DIR.
_SYSTEM_PROMPT_FILE = "cheese-system-prompt.md"
# Wait this long for the pane to reach the `❯` input box after (re)starting.
_READY_TIMEOUT_S = 45.0
_READY_POLL_S = 0.4
# Fixed pane geometry (`window-size manual`). Live 现场 viewers attach through
# ttyd as REAL tmux clients, and the default `window-size latest` handed each of
# them the pane geometry: a 46-column drawer shrank the running claude's
# composer, and every attach/detach/browser-resize fired a SIGWINCH re-render
# storm into the TUI mid-turn. The mirror is read-only — it gets a cropped view,
# not a vote on the geometry the agent actually runs in.
_PANE_COLS = 120
_PANE_ROWS = 40
# Paste → verify → Enter → verify pacing (see send_prompt). Budgeted so the
# whole worst case ((1+_MAX_REPASTES)·_PASTE_SETTLE_S + _MAX_ENTERS·_ENTER_SETTLE_S
# ≈ 19.5s) finishes — or fails loud — inside hooks_substrate.DELIVERY_TIMEOUT_S
# (25s), which stays the outer authority via the UserPromptSubmit receipt.
_PASTE_SETTLE_S = 4.0
_ENTER_SETTLE_S = 1.5
_SETTLE_POLL_S = 0.25
_MAX_REPASTES = 2
_MAX_ENTERS = 5
# Bound on the parent walk that finds a topic's room. Two would do for anything
# created today (a task's parent IS a room); the margin covers legacy `subtopic`
# rows, and the bound itself makes a cycle in the tree a degraded box placement
# rather than a hung turn.
_MAX_ROOM_WALK = 8

logger = logging.getLogger(__name__)


def _cheese_cli_mount(session_host: str) -> list[str]:
    """`-v <session>/bin/cheese:/usr/local/bin/cheese:ro`.

    The source is the project's SESSIONS ROOT, not one topic's session dir: a box
    serves a whole room and can mount only one CLI. It still has to be a staged
    copy rather than the image's baked one (see ws.sessions_root).

    The mount OVERRIDES the copy the sandbox image bakes at build time, and it
    is a REQUIREMENT, not an optimisation: an image is rebuilt on its own
    schedule, so the baked copy silently falls behind the backend that drives it
    (observed 2026-08-10 — the container ran a CLI whose `remember`/`recall` did
    not send `topic`, so every memory an agent wrote landed in the wrong pool).

    When the backend runs in a container it spawns the sandbox as a SIBLING, so
    the source must be a path the HOST daemon can see. The in-image
    `/app/sandbox/cheese` is not one (mounting it aborts the container with
    `not a directory`), and an operator-maintained host checkout — the old
    `sandbox_shim_host_dir` — is exactly what went stale. The sessions root is
    already a host bind-mount source AND is re-seeded from this build on every
    turn (ws.sessions_root), so sourcing from there is fresh by construction."""
    return [
        "-v",
        f"{ws.cheese_cli_mount_source(Path(session_host))}:/usr/local/bin/cheese:ro",
    ]


def _best_effort_chmod(path: Path, mode: int) -> None:
    """chmod that tolerates not owning the file. The session dir is shared with
    other uids across runs (the mount is a host path), so a file a previous run
    created under a different owner can't be chmod'd by this one — but the write
    already succeeded and the mode is only a nicety. EPERM here must not abort a
    turn (it did: 'Operation not permitted' on settings.json)."""
    try:
        path.chmod(mode)
    except OSError:
        pass


def _rewrite(path: Path, content: str, *, mode: int) -> None:
    """Replace a file the backend planted, even if the container's user (uid 1000
    in the sandbox image) rewrote it last turn under a different owner.

    The backend runs as one uid and the sandbox's Claude Code as another, both
    writing the SAME host-path session dir. So the settings/credential files this
    plants get re-owned by the container between turns, and a plain overwrite then
    fails EPERM. Unlinking first only needs write on the parent dir (which the
    backend owns), so the file is always recreated fresh under the backend."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    path.write_text(content, encoding="utf-8")
    _best_effort_chmod(path, mode)


def _resume_ready(session_dir: str, resume_session_id: str) -> bool:
    """True when a resumable transcript for ``resume_session_id`` is present in
    this topic's ~/.claude mount (i.e. a cloned/forked conversation was written
    there), under whatever slug it was written with. Pure so it can be
    unit-tested without a container. Guards the `--resume` path so an ordinary
    fresh topic (no transcript) never resumes."""
    return clone.find_transcript(Path(session_dir), resume_session_id) is not None


# Container label carrying the routing-env stamp (see _ensure_container).
_ENV_LABEL = "cheesex.env"

# Only the env that decides WHERE model calls go and as WHAT. Per-turn values
# (CHEESE_TURN) and anything that legitimately changes without invalidating the
# box must stay out, or every turn would rebuild the container.
_ENV_STAMPED_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CHEESE_API",
)


def _env_stamp(env: dict[str, str]) -> str:
    """A short digest of the routing-relevant env. Hashed rather than stored
    plainly because one of the values is a credential. Pure, so the drift rule
    is unit-testable without Docker."""
    material = "\n".join(f"{k}={env.get(k, '')}" for k in _ENV_STAMPED_KEYS)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def env_stamp_drifted(current: str, wanted: str) -> bool:
    """Whether a container's recorded model route disagrees with the wanted one.

    An UNSTAMPED container is not evidence of drift — it predates the stamp. It
    must be left alone, because rebuilding kills its tmux session and that
    session IS the topic's conversational continuity: treating "unknown" as
    "wrong" would silently reset every existing topic's memory on its next turn.
    Nothing is stranded by waiting, since the sandbox image tag carries the
    commit sha, so every container is rebuilt (and stamped) within one deploy.
    """
    return bool(current) and current != wanted


def pane_ready(capture: str) -> bool:
    """True when a captured tmux pane shows Claude Code's input box (the `❯`
    prompt) — the ready signal before injecting a prompt (spike 就绪握手). Pure so
    it can be unit-tested without a container."""
    return "❯" in capture


# --- prompt-delivery verification (pure, unit-tested) -----------------------
# Screen text is matched FLATTENED — whitespace and the composer's box-drawing
# borders stripped. The composer soft-wraps at the pane width, so on screen the
# body is interleaved with newlines, row padding and `│` borders; no single row
# can be trusted to show a whole anchor (24 CJK chars need 48 columns, and a
# viewer-shrunk 46-column pane never has them — the 2026-08-16 paste-loop
# outage). Must stay in lockstep with the device driver's norm()/bodyInComposer
# (cheeselets/claude_min.js): both backends judge "did my keystrokes take" the
# same way.
_BOX_CHARS = set("│╭╮╰╯─")


def _flatten(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace() and ch not in _BOX_CHARS)


def prompt_snippet(prompt: str) -> str:
    """The screen-verifiable anchor: the head of the prompt's first non-blank
    line, flattened, capped at 24 chars. Compared against flattened screen text
    only — never against a single row."""
    for line in prompt.splitlines():
        flat = _flatten(line)
        if flat:
            return flat[:24]
    return ""


def composer_holds_body(capture: str, snippet: str) -> bool:
    """True when the pasted prompt is visibly sitting in the input box: the
    composer is everything from the LAST `❯` on screen (history user messages
    render with `>`), and the body shows either literally or as Claude Code's
    `[Pasted text #N +N lines]` widget (large pastes render as that placeholder
    instead of the text — claude-session-driver #20)."""
    i = capture.rfind("❯")
    if i == -1:
        return False
    flat = _flatten(capture[i:])
    if "[Pastedtext" in flat:  # the placeholder, flattened like everything else
        return True
    return bool(snippet) and snippet in flat


@dataclass(frozen=True)
class TmuxScreen:
    """Where one topic's interactive `claude` lives: a box (shared with the rest
    of its room) and a tmux session inside it (that topic's alone).

    The screen ctx used to be just the container name, back when those were the
    same thing. They are not any more, and every `docker exec` below has to say
    which of the two it means — `container` for the box, `session` for `-t`."""

    container: str
    session: str


def _tmux_container_name(topic_id: uuid.UUID) -> str:
    """The box hosting this topic — its ROOM's, shared with the room's other
    topics. Source of truth for the NAME is ws.tmux_container_name (so the
    accept/archive reaper frees the same box); the topic→room step is
    ws.room_for_topic, whose marker this provider writes when it starts a box."""
    return ws.tmux_container_name(ws.room_for_topic(topic_id))


def _screen_for(topic_id: uuid.UUID) -> TmuxScreen:
    return TmuxScreen(_tmux_container_name(topic_id), ws.tmux_session_name(topic_id))


def ttyd_endpoint(topic_id: uuid.UUID) -> str | None:
    """`127.0.0.1:<host-port>` of the topic's ttyd (the read-only terminal mirror
    of its pane), or None when the box is down / the port isn't published (a box
    predating the publish). Same `docker port` parse as workspace.app_endpoint —
    used by the 施工现场 terminal proxy to reach the topic's live pane.

    One ttyd per TOPIC, not per box: a box hosts a whole room, and a mirror that
    showed a sibling's pane would be worse than none. Which published port is
    this topic's follows from the slot its session holds."""
    if not ws.sandbox_available():
        return None
    return ws.published_endpoint(
        _tmux_container_name(topic_id),
        ws.ttyd_port_for_slot(ws.topic_port_slot(topic_id)),
    )


def _hook_base() -> str:
    """Backend base URL reachable from the container — the app root.

    Since #370 step 2 that is simply the configured base; the stripping lives in
    `settings.agent_api_base()` so a box whose .env still carries the old
    `…/api` value keeps working in one place rather than three.
    """
    return settings.agent_api_base()


def _subscription_args() -> list[str]:
    """Docker args that route this sandbox's model calls through the meter.

    The capture is by NAME, not by proxy env. When this was built (2026-08-03),
    the node-built CLI issued model calls through undici, which ignored
    HTTPS_PROXY (measured — the proxy saw every auxiliary request and never a
    single /v1/messages, while the turns kept answering). Resolving
    api.anthropic.com to the meter catches every runtime, because that path goes
    through DNS. The CURRENT CLI is the native build and does honor HTTPS_PROXY
    (re-measured 2026-08-13 on 2.1.229 — the device path relies on that, see
    provider_env.subscription_provider), but --add-host stays the container
    transport: it is version-independent and captures nothing but the three
    Anthropic names.

    Only the CA is mounted. The real credential is NEVER placed in the container
    (hard requirement: a machine must not hold a valid credential). Login is a
    placeholder CLAUDE_CODE_OAUTH_TOKEN in the env (see subscription_provider),
    and the metering proxy swaps the Authorization header for the real token,
    which lives only on the backend. That also makes refresh single-point — one
    daemon owns the real credential, so no sandbox ever touches it.
    """
    if not settings.subscription_enabled:
        return []
    host = settings.subscription_proxy_host
    # api.anthropic.com carries the messages (metered); console.anthropic.com and
    # platform.claude.com carry interactive Claude Code's login/refresh. All go
    # to the same proxy, which routes each to its real host by SNI and injects the
    # real token — so the login check passes without a valid credential in the box.
    args: list[str] = []
    for h in ("api.anthropic.com", "console.anthropic.com", "platform.claude.com"):
        args += ["--add-host", f"{h}:{host}"]
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


# How big the container actually is. Named rather than inlined because the
# agent inside has to be TOLD: it cannot see its own cgroup limit, and the
# failure it produces without knowing — a build the kernel OOM-kills — reads
# like a broken toolchain rather than a small box. `_turn_meta_lines` in chat.py
# puts it in the prompt as a run fact, next to the time budget and disk
# headroom, which is the same category: things an agent has no other way to see.
#
# Derived from settings rather than hardcoded because the box is now a ROOM's,
# not a topic's, and its budget is a deployment knob (settings.sandbox_memory_gb).
# The `--memory`/`--cpus` args below read THESE constants, not the settings
# directly: the number the agent is told and the number the kernel enforces have
# to come from one place, which is exactly what
# test_the_stated_size_is_the_one_the_container_actually_gets guards.
#
# Cores is floored to an int because chat.py only speaks up when it gets one
# (`isinstance(cores, int)`) — a fractional quota would silence the prompt
# entirely, which is worse than telling the agent 2 when it has 2.5.
SANDBOX_MEMORY_MB = int(settings.sandbox_memory_gb * 1024)
SANDBOX_CORES = int(settings.sandbox_cpus)


class TmuxChannel(Channel):
    """The LOCAL channel: a per-topic tmux session inside a platform container
    on this host, reached with ``docker exec``. The screen is a (container,
    session) pair — the box belongs to the topic's room, the session to the
    topic.

    Docker, worktrees, port slots, ttyd and window sizes are what this file is
    about. What runs on the screen, and everything that senses it, is
    ``ClaudeCodeRuntime``."""

    # (see _subscription_args below for how a subscription turn is captured)

    name = "tmux-hooks"
    # What this backend's container is capped at. Read by chat.py through a
    # getattr, so a backend that genuinely does not know its own size (an
    # enrolled machine belongs to someone else) simply says nothing rather than
    # guessing — an invented limit would be worse than none.
    sandbox_memory_mb = SANDBOX_MEMORY_MB
    sandbox_cores = SANDBOX_CORES
    needs_topic_message = "tmux 后端需要 Docker 和话题上下文（缺一不可）"
    timeout_message = "tmux 轮次超时"

    def __init__(
        self,
        *,
        image: str,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self._image = image
        # Only `_room_id` uses it — the transport itself is DB-free, but which
        # BOX a topic belongs to is a fact about the topic tree. Injectable for
        # the same reason as CloudChannel's: the process-wide factory points at
        # the deployment's database, which a test is not running against.
        self._session_factory = session_factory
        # One control-mode connection per SCREEN, reused across turns — the
        # client attaches to a named session, so a box hosting a room needs one
        # per topic, not one per box.
        self._controls: dict[TmuxScreen, TmuxControlClient] = {}
        # Screen → the running turn's ActivityTracker (turn 活跃度检测), for
        # `cheese status` to read via `activity_status()`. Populated by
        # `start_activity_monitor` for exactly as long as its turn runs.
        self._activity: dict[TmuxScreen, ActivityTracker] = {}

    def available(self) -> bool:
        return ws.sandbox_available()

    async def discover(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, uuid.UUID, object | None]]:
        """Every still-running topic session on this host after a restart.

        Walks BOXES for the project, then SESSIONS inside each for the topics —
        the topic id is per-session now, so the old shortcut of reading
        CHEESE_TOPIC off the container environment would recover exactly one of a
        room's topics and silently orphan the rest."""
        if device_id is not None:
            return []
        rc, out, err = await _docker(
            "ps", "--filter", "label=cheesex-tmux=1", "--format", "{{.Names}}"
        )
        if rc != 0:
            logger.warning(
                "tmux subscription recovery could not list containers: %s", err
            )
            return []

        found: list[tuple[uuid.UUID, uuid.UUID, object | None]] = []
        for name in (line.strip() for line in out.splitlines()):
            if not name:
                continue
            env_rc, env_out, env_err = await _docker(
                "inspect",
                "-f",
                "{{range .Config.Env}}{{println .}}{{end}}",
                name,
            )
            if env_rc != 0:
                logger.warning(
                    "tmux subscription recovery could not inspect %s: %s",
                    name,
                    env_err,
                )
                continue
            env: dict[str, str] = {}
            for line in env_out.splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    env[key] = value
            try:
                project_id = uuid.UUID(env["CHEESE_PROJECT"])
            except (KeyError, ValueError):
                logger.warning(
                    "tmux subscription recovery skipped %s with invalid scope", name
                )
                continue
            for session, topic_id in await self._live_sessions(name):
                found.append((project_id, topic_id, TmuxScreen(name, session)))
        return found

    async def _live_sessions(self, container: str) -> list[tuple[str, uuid.UUID]]:
        """(session name, topic id) for every cheese session in a box.

        The topic id comes from the session's OWN environment rather than from
        parsing its name: the name only carries 8 hex characters (enough to be
        unique on disk, not enough to rebuild a uuid), and the env is the same
        value `claude` is actually running on."""
        rc, out, _ = await _docker(
            "exec", container, "tmux", "list-sessions", "-F", "#{session_name}"
        )
        if rc != 0:
            return []  # no tmux server yet, or the box is not running
        found: list[tuple[str, uuid.UUID]] = []
        for session in (line.strip() for line in out.splitlines()):
            if not session.startswith(ws.LEGACY_TMUX_SESSION):
                continue
            raw = await self._session_env_var(
                TmuxScreen(container, session), "CHEESE_TOPIC"
            )
            if raw is None:
                continue
            with contextlib.suppress(ValueError):
                found.append((session, uuid.UUID(raw)))
        return found

    @staticmethod
    async def _session_env_var(screen: TmuxScreen, key: str) -> str | None:
        """One variable out of a session's own environment (host-side view of
        what that session's `claude` was launched with), or None."""
        rc, out, _ = await _docker(
            "exec",
            screen.container,
            "tmux",
            "show-environment",
            "-t",
            screen.session,
            key,
        )
        if rc != 0:
            return None
        # "KEY=value" when set; "-KEY" when explicitly unset.
        name, separator, value = out.strip().partition("=")
        return value if separator and name == key else None

    # --- container / session lifecycle -------------------------------------

    async def _ensure_container(
        self, topic_id: uuid.UUID, room_id: uuid.UUID, env: dict[str, str]
    ) -> str:
        """Create (or reuse) the ROOM's tmux container and return its name.

        Env is fixed at CREATION and the container is long-lived, so a box
        created against an old model route keeps using it no matter what the
        backend is reconfigured to — a fixed deployment stays stranded behind a
        stale container (dev, 2026-08-08: a corrected gateway URL had no effect
        because the running `claude` still held the old one). The routing part of
        the env is therefore stamped on the container and rechecked here.

        Only room-wide env is stamped, and that is not a coincidence: everything
        per-topic left the container environment for the tmux session precisely
        so that one topic's turn can never invalidate the box its siblings are
        working in."""
        name = ws.tmux_container_name(room_id)
        rc, cur_image, _ = await _docker("inspect", "-f", "{{.Config.Image}}", name)
        exists = rc == 0
        image_switched = exists and cur_image.strip() != self._image
        env_drifted = False
        cli_mount_stale = False
        if exists and not image_switched:
            _, cur_stamp, _ = await _docker(
                "inspect", "-f", f'{{{{index .Config.Labels "{_ENV_LABEL}"}}}}', name
            )
            env_drifted = env_stamp_drifted(cur_stamp.strip(), _env_stamp(env))
            # Mounts are fixed at creation, so a box built before the CLI mount
            # moved to the session dir would keep serving the old source (or the
            # image's baked copy) for the life of the topic — the very staleness
            # this mount exists to prevent. Recheck it like the model route.
            cli_mount_stale = await self._cli_mount_stale(name, env["SBX_SESSIONS"])
        # First match wins, so the room is told the most specific thing that is
        # true. None means "nothing was torn down" — either the box is fine, or
        # this is its first creation.
        cause = next(
            (
                c
                for c, hit in (
                    ("image", image_switched),
                    ("env", env_drifted),
                    ("cli_mount", cli_mount_stale),
                )
                if hit
            ),
            None,
        )
        bereaved: list[uuid.UUID] = []
        if cause is not None:
            # WHO loses a session, recorded before the box goes: a box serves a
            # whole room, so destroying it ends every topic's conversation in
            # it, not just the one whose turn happened to notice. Announcing
            # only to this topic would leave the siblings' agents restarted with
            # no memory and nothing in their rooms saying why.
            bereaved = [topic for _, topic in await self._live_sessions(name)]
            await self.drop_container_controls(name)
            await _docker("rm", "-f", name)
            exists = False
        if not exists:
            await self._create_container(name, env, topic_id)
            if cause is not None:
                # The old box (and anything running in it — the interactive
                # sessions, background processes) is gone with no other
                # warning; tell every topic that had one (best-effort, never
                # blocks the turn). `cause` is None only on a FIRST creation,
                # where nothing was destroyed and there is nothing to announce.
                for topic in dict.fromkeys([topic_id, *bereaved]):
                    await warn_container_rebuilt(topic, cause)
            return name
        rc, running, _ = await _docker("inspect", "-f", "{{.State.Running}}", name)
        if running.strip() != "true":
            await _docker("start", name)
        return name

    async def _hook_token_dead(self, screen: TmuxScreen) -> bool:
        """True when the token this SESSION's `claude` is running on no longer
        verifies.

        This is a topic going DEAF, and until it was checked nothing ever
        noticed. The hook forwarder sends that token on every event; the
        endpoint 401s a token it cannot verify and returns — no log, no spool,
        no listener. The turn then runs to its ceiling having observed nothing:
        `first_output_s: null`, `tools: 0`, while `claude` inside works
        perfectly.

        The token is written into the session environment at session CREATION
        and never refreshed, because the running `claude` read it once at exec.
        Its 30-day TTL is not what expires it — the SIGNING SECRET was, when it
        fell back to a fresh random per backend process and so invalidated every
        live session at once on every restart. That root cause is fixed
        (`Settings.sandbox_signing_secret`), and this check stays as the backstop
        for what remains: a deliberately rotated SANDBOX_TOKEN, a token past its
        TTL, a session restored from an older deployment.

        Recovering costs only the SESSION now — its conversational continuity,
        not the box, and none of its siblings' work. That is a far cheaper
        remedy than the container rebuild this used to force, which is the other
        reason the per-topic state had to move off the container."""
        baked = await self._session_env_var(screen, "CHEESE_TOKEN")
        project = await self._session_env_var(screen, "CHEESE_PROJECT")
        topic = await self._session_env_var(screen, "CHEESE_TOPIC")
        if not baked or not project or not topic:
            # A session predating scoped hook tokens. Unknown is not evidence of
            # death — same rule as the env stamp, and for the same reason.
            return False
        return not verify_scoped_token(baked, project_id=project, topic_id=topic)

    @staticmethod
    async def _cli_mount_stale(name: str, sessions_host: str) -> bool:
        """True when the box's /usr/local/bin/cheese does not come from THIS
        build's staged copy (missing mount, or an old source path)."""
        rc, out, _ = await _docker(
            "inspect",
            "-f",
            '{{range .Mounts}}{{.Source}}->{{.Destination}}{{"\\n"}}{{end}}',
            name,
        )
        if rc != 0:
            return False  # can't tell — don't destroy a box on a failed inspect
        staged = ws.cheese_cli_mount_source(Path(sessions_host))
        return f"{staged}->/usr/local/bin/cheese" not in out.splitlines()

    async def _create_container(
        self, name: str, env: dict[str, str], mount_anchor: uuid.UUID
    ) -> None:
        """Create the ROOM's box. Everything in `env` is room-wide by
        construction — a per-topic value baked in at creation would be frozen
        for every topic that joins later, which is why the caller passes only
        room env (see `_room_env`).

        ``mount_anchor`` is the topic whose worktree the jj/git remap is
        computed against. Any topic of the project gives the same answer (they
        sit at equal depth under one tree — that is what lets a room share a box
        at all), so it must be one whose worktree EXISTS: the caller's, not the
        room's. A room that has never taken a turn has no worktree, and
        computing against a missing directory is the quiet half of the problem —
        the loud half is `-w`, below."""
        # SBX_SESSIONS is a mount source, not a container env var.
        mounts = {"SBX_SESSIONS"}
        project_id = uuid.UUID(env["CHEESE_PROJECT"])
        # One mount of the project's whole `.worktrees` tree (every topic's
        # worktree and the shared pnpm/uv stores) plus the main repo's .jj/.git
        # remap — see ws.sandbox_project_mounts for why a single mount is
        # load-bearing (hardlinks cannot cross bind mounts) and what it means
        # for same-project isolation.
        project_mounts = ws.sandbox_project_mounts(project_id, mount_anchor)
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--user",
            "node",
            "--add-host",
            "host.docker.internal:host-gateway",
            # The project's whole sessions tree, for the same reason as the
            # worktrees tree: mounts are fixed at creation and a room keeps
            # gaining topics. Each session points its own claude at its own
            # subdirectory via CLAUDE_CONFIG_DIR.
            "-v",
            f"{env['SBX_SESSIONS']}:{ws.SANDBOX_SESSIONS_ROOT}",
            *_subscription_args(),
            *project_mounts,
            # The TREE, not a topic's directory inside it. `docker run -w`
            # CREATES a missing workdir, and this one lands in a bind mount — so
            # naming a topic here would plant an empty directory on the host
            # exactly where that topic's jj workspace has to go, and
            # `jj workspace add` refuses a path that already exists. Each
            # session sets its own cwd with `new-session -c` anyway.
            "-w",
            ws.SANDBOX_TOPICS_ROOT,
        ]
        # One app port + one ttyd port per slot, published up front: a published
        # port cannot be added to a running container, and the topics that will
        # need them do not exist yet when the box is created.
        for slot in range(ws.port_slots()):
            args += ["-p", f"127.0.0.1:0:{ws.app_port_for_slot(slot)}"]
            args += ["-p", f"127.0.0.1:0:{ws.ttyd_port_for_slot(slot)}"]
        args += _cheese_cli_mount(env["SBX_SESSIONS"])
        args += [
            "--network",
            "bridge",
            # A ROOM budget, not a topic's — the box runs the room's whole
            # concurrency now. See settings.sandbox_memory_gb for the sizing.
            "--memory",
            f"{SANDBOX_MEMORY_MB}m",
            "--cpus",
            str(SANDBOX_CORES),
            "--pids-limit",
            str(settings.sandbox_pids_limit),
            # A crashing node/vite process must not dump its address space into
            # the worktree (1-2GB core files were a top disk consumer on dev).
            "--ulimit",
            "core=0",
            "--label",
            "cheesex-sandbox=1",
            "--label",
            "cheesex-tmux=1",
        ]
        for key, value in env.items():
            if key in mounts:
                continue
            args += ["-e", f"{key}={value}"]
        # Records WHICH model route this box was built for, so a later turn can
        # tell a still-correct container from one the backend has outgrown.
        args += ["--label", f"{_ENV_LABEL}={_env_stamp(env)}"]
        args += [self._image, "sleep", "infinity"]
        rc, _, err = await _docker(*args)
        if rc != 0:
            raise RuntimeError(f"tmux container create failed: {err.strip()}")

    async def _allocate_port_slot(self, screen: TmuxScreen) -> int:
        """The published-port slot this topic holds, allocating one if it has
        none: the lowest slot no live session in this box already holds.

        The tmux server is the registry — each session carries its slot in its
        own environment — so the answer cannot drift from the set of sessions
        that actually exist, and a session dying frees its slot with no
        bookkeeping. Returns `port_slots() - 1` when the block is exhausted:
        the session still runs and still talks to the platform, it just shares a
        preview port with another topic, which is a far better failure than
        refusing the turn.

        An existing session answers from its own environment in one call, which
        is the common path — every turn after the first. Scanning the whole
        room here would cost two `docker exec`s per sibling on every turn, to
        compute a number the caller then discards because the session is
        already running on it."""
        mine = await self._session_env_var(screen, "CHEESE_PORT_SLOT")
        if mine is not None and mine.isdigit():
            return int(mine)
        taken: set[int] = set()
        for session, _ in await self._live_sessions(screen.container):
            if session == screen.session:
                continue
            raw = await self._session_env_var(
                TmuxScreen(screen.container, session), "CHEESE_PORT_SLOT"
            )
            if raw is not None and raw.isdigit():
                taken.add(int(raw))
        slots = ws.port_slots()
        free = next((s for s in range(slots) if s not in taken), None)
        if free is None:
            logger.warning(
                "room box %s has no free port slot (%d in use); %s shares slot %d",
                screen.container,
                len(taken),
                screen.session,
                slots - 1,
            )
            return slots - 1
        return free

    async def _retire_legacy_session(self, container: str) -> None:
        """Kill the box's pre-room session, if it still has one.

        A box built before per-topic sessions holds one session literally named
        ``cheese``, which no topic will ever look up again — so without this it
        would keep a `claude` alive, holding the app/ttyd port and writing the
        worktree, beside the new session for the same topic. In practice such a
        box is rebuilt on its first turn anyway (its CLI mount source moved), so
        this only matters when that rebuild does not happen — which is exactly
        when two agents in one box would be hardest to notice.

        Matched EXACTLY against the session list rather than passed to `tmux
        has-session`: tmux falls back to PREFIX matching, and ``cheese`` is a
        prefix of every new session name, so asking tmux directly would report a
        live legacy session that isn't there — and `kill-session -t cheese`
        would then destroy a real topic's session."""
        rc, out, _ = await _docker(
            "exec", container, "tmux", "list-sessions", "-F", "#{session_name}"
        )
        if rc != 0:
            return
        if ws.LEGACY_TMUX_SESSION not in [line.strip() for line in out.splitlines()]:
            return
        await _docker(
            "exec", container, "tmux", "kill-session", "-t", ws.LEGACY_TMUX_SESSION
        )

    async def _ensure_session(
        self,
        screen: TmuxScreen,
        model: str | None,
        *,
        session_env: dict[str, str],
        resume_session_id: str | None = None,
        session_dir: str | None = None,
        system_prompt: str = "",
    ) -> None:
        """Ensure the topic's interactive `claude` tmux session exists (lazy,
        reused).

        Normally the tmux session IS the continuity, so this starts a FRESH
        `claude`. The ONE exception (enabling clone, fusion-design §6): when a
        resumable session id is given AND its transcript is actually present in
        this topic's config dir, start `claude --resume <id>` so a cloned
        (transcript-fork) conversation is picked up on the target topic's first
        turn. The transcript-existence guard keeps every normal path unchanged —
        a fresh topic has no transcript, so it never accidentally resumes."""
        rc, _, _ = await _docker(
            "exec", screen.container, "tmux", "has-session", "-t", screen.session
        )
        if rc == 0:
            if await self._hook_token_dead(screen):
                # Deaf: this session can never report anything again, so keeping
                # it costs more than the continuity restarting it loses. Only
                # this session goes — the box and its siblings are untouched.
                await self.drop_control(screen)
                await _docker(
                    "exec",
                    screen.container,
                    "tmux",
                    "kill-session",
                    "-t",
                    screen.session,
                )
                # The conversation is gone with no other warning; tell the topic
                # (best-effort, never blocks the turn).
                await warn_container_rebuilt(
                    uuid.UUID(session_env["CHEESE_TOPIC"]), "token"
                )
            else:
                # Re-pin every turn: a session created before the geometry pin
                # (or already shrunk by a Live 现场 viewer under `window-size
                # latest`) must be brought back to the fixed size, not locked
                # into the viewer's — see _PANE_COLS.
                await self._pin_window_size(screen)
                return
        await self._retire_legacy_session(screen.container)
        claude_cmd = CLAUDE_BASE_CMD
        # The platform's system prompt, written into the session dir by
        # ensure_ready. Only a FRESH claude reads it — an already-running
        # session keeps the prompt it launched with (same as settings.json).
        if system_prompt:
            claude_cmd += (
                f" --append-system-prompt-file {session_env['CLAUDE_CONFIG_DIR']}"
                f"/{_SYSTEM_PROMPT_FILE}"
            )
        if (
            resume_session_id
            and session_dir
            and _resume_ready(session_dir, resume_session_id)
        ):
            claude_cmd += f" --resume {resume_session_id}"
        # Pass --model when set. On the subscription this is the project's pick
        # ("opus"; empty = the subscription's default Sonnet, so no flag). On the
        # gateway it's the gateway model name. Either way, an empty model means
        # "use the default" — never pin a name the provider does not serve.
        if model:
            claude_cmd += f" --model {model}"
        args = [
            "exec",
            screen.container,
            "tmux",
            "new-session",
            "-d",
            "-x",
            str(_PANE_COLS),
            "-y",
            str(_PANE_ROWS),
            "-s",
            screen.session,
            "-c",
            session_env["CHEESE_WORKDIR"],
        ]
        # EXPLICITLY per key, never by inheritance. A new tmux session seeds its
        # environment from the SERVER's global one, frozen when that server
        # first started — so in a box that already hosts another topic, a fresh
        # `claude` would boot on the FIRST topic's id, token and config dir and
        # report every event as that topic. The device backend measured exactly
        # this on a shared machine (device_launch.py: "/proc of topic B's claude
        # showed topic A's HOME and PATH"). `-e` writes the session env before
        # claude execs, so each topic runs on its own.
        for key, value in session_env.items():
            if value:
                args += ["-e", f"{key}={value}"]
        args.append(claude_cmd)
        rc, _, err = await _docker(*args)
        if rc != 0:
            raise RuntimeError(f"tmux new-session failed: {err.strip()}")
        await self._pin_window_size(screen)
        # ttyd (施工现场): a read-only mirror of THIS topic's pane, on this
        # topic's own published port. Best-effort — the turn does not depend on
        # it. The pgrep guard matches the port so a second topic in the same box
        # still gets its own ttyd instead of finding the first one's and
        # skipping.
        ttyd_port = ws.ttyd_port_for_slot(int(session_env["CHEESE_PORT_SLOT"]))
        await _docker(
            "exec",
            "-d",
            screen.container,
            "sh",
            "-lc",
            f"pgrep -f 'ttyd -R -p {ttyd_port} ' >/dev/null 2>&1 || "
            f"ttyd -R -p {ttyd_port} tmux attach -t {screen.session}",
        )

    async def _pin_window_size(self, screen: TmuxScreen) -> None:
        """Fix the session's geometry at `_PANE_COLS`×`_PANE_ROWS` and stop any
        attached client (the ttyd mirror) from ever changing it again. Both
        commands are strict: the image ships tmux 3.3a, which supports both, so
        a failure here is a real fault, not a version gap.

        `set-option -g` is the server's global default and therefore room-wide,
        which is fine: every session in the box wants the same fixed geometry,
        and the resize below is per session anyway."""
        rc, _, err = await _docker(
            "exec",
            screen.container,
            "tmux",
            "set-option",
            "-g",
            "window-size",
            "manual",
        )
        if rc != 0:
            raise RuntimeError(f"tmux set-option window-size failed: {err.strip()}")
        rc, _, err = await _docker(
            "exec",
            screen.container,
            "tmux",
            "resize-window",
            "-t",
            screen.session,
            "-x",
            str(_PANE_COLS),
            "-y",
            str(_PANE_ROWS),
        )
        if rc != 0:
            raise RuntimeError(f"tmux resize-window failed: {err.strip()}")

    async def _capture_pane(self, screen: TmuxScreen) -> str | None:
        """The pane's visible text, or None on a transient docker/tmux failure."""
        rc, out, _ = await _docker(
            "exec", screen.container, "tmux", "capture-pane", "-p", "-t", screen.session
        )
        return out if rc == 0 else None

    async def _wait_ready(self, screen: TmuxScreen) -> bool:
        """Poll capture-pane until the `❯` input box appears (spike 就绪握手)."""
        deadline = asyncio.get_event_loop().time() + _READY_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            capture = await self._capture_pane(screen)
            if capture is not None and pane_ready(capture):
                return True
            await asyncio.sleep(_READY_POLL_S)
        return False

    async def _control(self, screen: TmuxScreen) -> TmuxControlClient:
        """The screen's control-mode client, created once and reused.

        One long-lived connection instead of a `docker exec` per keystroke
        batch: every command comes back as %end or %error, so a failed
        injection is distinguishable from a successful one. Per SCREEN, not per
        box — the client attaches to one named session."""
        client = self._controls.get(screen)
        if client is not None and client.alive:
            return client
        if client is not None:
            await client.close()
        client = TmuxControlClient(
            None,
            screen.session,
            spawn_prefix=["docker", "exec", "-i", screen.container],
        )
        await client.start()
        self._controls[screen] = client
        return client

    async def drop_control(self, screen: TmuxScreen) -> None:
        """Forget a screen's control and subscription before it goes away."""
        await drop_screen_subscriptions(screen)
        client = self._controls.pop(screen, None)
        if client is not None:
            await client.close()

    async def drop_container_controls(self, container: str) -> None:
        """Forget every screen in a box — used when the BOX itself is about to be
        destroyed, which takes all of its room's sessions with it."""
        for screen in [s for s in self._controls if s.container == container]:
            await self.drop_control(screen)

    async def _await_composer(
        self, screen: TmuxScreen, snippet: str, *, holds: bool
    ) -> bool:
        """Poll the pane until the composer visibly holds (``holds=True``, after
        a paste) or lets go of (``holds=False``, after an Enter) the prompt body,
        bounded by the matching settle window. A transient capture failure is
        just another poll — never evidence either way."""
        settle = _PASTE_SETTLE_S if holds else _ENTER_SETTLE_S
        deadline = asyncio.get_event_loop().time() + settle
        while True:
            capture = await self._capture_pane(screen)
            if capture is not None and composer_holds_body(capture, snippet) == holds:
                return True
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await asyncio.sleep(_SETTLE_POLL_S)

    async def send_interrupt(self, screen: TmuxScreen) -> bool:
        """Escape into the pane — the key a person watching would press. Sent
        with `send-keys`, the same way this backend types anything else."""
        control = await self._control(screen)
        result = await control.send("send-keys", "-t", screen.session, "Escape")
        return bool(result.ok)

    async def send_prompt(
        self, screen: TmuxScreen, prompt: str, images: list[dict] | None = None
    ) -> None:
        """Inject the prompt as one atomic paste, then a SEPARATE Enter (spike:
        bracketed paste + independent Enter, so the prompt isn't split) — and
        confirm EACH half against the screen before moving on (the device
        cheeselet's #430 lesson, ported).

        Two ways a "successful" send delivers nothing, both measured: tmux
        accepts a send into a pane whose process has exited and reports SUCCESS
        (tests/unit/test_tmux_control.py — hence the live-pane check before
        pasting; a dead session once swallowed turns silently until the 900s
        ceiling, dev 2026-08-08). And Claude Code itself swallows an Enter that
        arrives while it is still ingesting the paste (claude-session-driver
        #20) — the prompt then sits in the composer forever, which is exactly
        what a zero-delay paste→Enter raced into whenever the TUI was busy
        (cold start, Live 现场 resize storms). So: paste → wait until the body
        is visibly in the composer → Enter → re-send the Enter until the
        composer visibly lets go. Re-sending Enter is duplication-safe (a lone
        Enter on an empty composer is a no-op); re-PASTING is not, so only the
        body-never-appeared case pastes again, and everything after that only
        nudges Enter. The UserPromptSubmit hook stays the delivery authority —
        this loop exists so the 25s verdict stops firing on a swallowed
        keystroke."""
        del images  # files already live in the shared topic worktree
        try:
            control = await self._control(screen)
            if await control.pane_dead():
                raise ScreenSetupError(
                    "tmux 会话的窗格已经死掉（里面的 claude 不在了），本轮未发送"
                )
            snippet = prompt_snippet(prompt)
            for _ in range(1 + _MAX_REPASTES):
                # Clear the composer before EVERY paste attempt (Ctrl+U —
                # measured on 2.1.233: kills the whole input including a
                # `[Pasted text …]` widget, is a no-op when empty, and unlike
                # Esc/Ctrl+C carries no "press again" arming or exit
                # semantics). The backend is this terminal's only writer, so
                # anything already sitting in the composer is residue of a
                # FAILED send — the poison that stacked 44 paste widgets in
                # prod (2026-08-17): leftover garbage let the paste-verify
                # pass on an OLD widget, and every retry appended instead of
                # replacing. Clearing first makes each attempt idempotent and
                # the verify unambiguous.
                clear = await control.send("send-keys", "-t", screen.session, "C-u")
                if not clear.ok:
                    raise ScreenSetupError(f"tmux 清空输入框失败：{clear.error}")
                # load-buffer reads the prompt on stdin, so it stays a docker
                # exec; everything with a meaningful failure mode goes over the
                # control socket.
                rc, _, err = await _docker(
                    "exec",
                    "-i",
                    screen.container,
                    "tmux",
                    "load-buffer",
                    "-",
                    stdin=prompt.encode(),
                )
                if rc != 0:
                    raise ScreenSetupError(f"tmux load-buffer 失败：{err.strip()}")
                paste = await control.send(
                    "paste-buffer", "-t", screen.session, "-d", "-p"
                )
                if not paste.ok:
                    raise ScreenSetupError(f"tmux 粘贴失败：{paste.error}")
                if await self._await_composer(screen, snippet, holds=True):
                    break
            else:
                raise ScreenSetupError(
                    "提示词粘贴后始终没有出现在输入框里（终端丢弃了粘贴），本轮未发送"
                )
            for _ in range(_MAX_ENTERS):
                enter = await control.send("send-keys", "-t", screen.session, "Enter")
                if not enter.ok:
                    raise ScreenSetupError(f"tmux 回车失败：{enter.error}")
                if await self._await_composer(screen, snippet, holds=False):
                    return
            raise ScreenSetupError(
                "回车补发多次后提示词仍留在输入框里（会话没有接受提交），本轮未发送"
            )
        except ScreenSetupError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed send ends the turn
            raise ScreenSetupError(f"tmux 后端启动失败：{exc}") from exc

    # --- activity detection (turn 活跃度检测, 2026-08-09) -------------------

    # How often the background monitor re-captures the pane. Independent of
    # `CONFIRM_POLL_S` (hooks_substrate) — this one just watches for output
    # changes; that one re-checks liveness once idle-suspect is already tripped.
    _ACTIVITY_POLL_S = 12.0

    async def _monitor_activity(
        self, screen: TmuxScreen, tracker: ActivityTracker
    ) -> None:
        """Background loop for `start_activity_monitor`: captures the pane every
        `_ACTIVITY_POLL_S` and touches `tracker` whenever the content changes —
        so a long tool call with no interim hook still counts as "alive" as long
        as the pane keeps producing output, not just on hook arrivals. Registers
        itself under `self._activity` (keyed by container name) for `cheese
        status` to read via `activity_status()`, for exactly as long as this
        turn's monitor runs."""
        self._activity[screen] = tracker
        last_hash: str | None = None
        try:
            while True:
                await asyncio.sleep(self._ACTIVITY_POLL_S)
                rc, out, _ = await _docker(
                    "exec",
                    screen.container,
                    "tmux",
                    "capture-pane",
                    "-p",
                    "-t",
                    screen.session,
                )
                if rc != 0:
                    continue  # transient docker hiccup — never treated as "died"
                digest = hashlib.sha256(out.encode()).hexdigest()
                if digest != last_hash:
                    last_hash = digest
                    tracker.touch(asyncio.get_event_loop().time())
        finally:
            self._activity.pop(screen, None)

    async def start_activity_monitor(
        self, screen: TmuxScreen, tracker: ActivityTracker
    ) -> asyncio.Task | None:
        return asyncio.create_task(self._monitor_activity(screen, tracker))

    async def confirm_alive(self, screen: TmuxScreen) -> bool:
        """The idle-suspect probe: a live, on-demand confirmation distinct from
        the passive capture-pane polling above — reuses the same `pane_dead()`
        check `send_prompt` already trusts before pasting. Best-effort: a
        control-connection hiccup is not evidence of death (mirrors `send_prompt`
        treating a send failure, not a probe failure, as fatal)."""
        try:
            control = await self._control(screen)
            return not await control.pane_dead()
        except Exception:  # noqa: BLE001 — a probe failure isn't proof of death
            return True

    def activity_status(self, topic_id: uuid.UUID) -> dict | None:
        """Snapshot of the running turn's activity tracker for `cheese status`
        (`/topics/{id}/status`), or None when no tmux turn is currently being
        monitored for this topic (not running, mid-setup before the monitor
        starts, or already finished)."""
        tracker = self._activity.get(_screen_for(topic_id))
        if tracker is None:
            return None
        now = asyncio.get_event_loop().time()
        return {
            "idle_for_s": round(now - tracker.last_at),
            "suspect_since_s_ago": (
                round(now - tracker.suspect_since)
                if tracker.suspect_since is not None
                else None
            ),
        }

    # --- turn --------------------------------------------------------------

    def _room_env(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        sessions_root: str,
        env: dict[str, str] | None,
    ) -> dict[str, str]:
        """Environment baked into the ROOM's container at creation.

        Strictly the part that is the same for every topic the box hosts: the
        model route, the callback base, the room's own identity. A per-topic
        value here would be frozen for every task that joins the room later —
        and worse, silently WRONG for it, since container env is what a process
        inherits when nothing overrides it. Everything per-topic is in
        `_topic_env` and reaches `claude` through `tmux new-session -e`.

        SBX_SESSIONS rides along as the sessions-tree mount source (stripped
        before -e)."""
        if settings.subscription_enabled:
            merged = {**(env or {})}
            for k in (
                "ANTHROPIC_BASE_URL",
                "CLAUDE_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
            ):
                merged.pop(k, None)
        else:
            merged = {**settings.agent_env(), **(env or {})}
        merged.update(
            {
                "HOME": "/home/node",
                "SBX_SESSIONS": sessions_root,
                "CHEESE_API": settings.agent_api_base(),
                "CHEESE_PROJECT": str(project_id),
                "CHEESE_ROOM": str(room_id),
            }
        )
        return merged

    def _topic_env(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        token: str,
        port_slot: int,
        env: dict[str, str] | None,
        memory_scope: str | None,
        owner: str | None,
        turn_id: uuid.UUID | None,
    ) -> dict[str, str]:
        """Environment written into the TOPIC's tmux session, per key, before its
        `claude` execs.

        This is the isolation boundary inside a shared box. Every value here
        answers "which topic is this" in some form — identity, credential,
        callback URL, config dir, cwd, published port — and getting any of them
        by inheritance instead means one topic acting as another. The device
        backend measured that exact failure on a shared machine, which is why
        `_ensure_session` passes all of it with `-e` rather than trusting the
        tmux server's frozen global environment."""
        if settings.subscription_enabled:
            # Subscription: point Claude Code at the metering proxy, trust its CA
            # (mounted by _subscription_args), attribute to this topic. No gateway
            # key, no model pin — see subscription_provider. The container also
            # ships a fake credential (see _write_session_settings); the real one
            # never leaves the backend.
            #
            # The subscription env WINS over the caller's `env`: that env carries
            # the gateway's ANTHROPIC_BASE_URL/token (the default provider), and
            # letting it override would send the turn to the GLM gateway instead
            # of the metering proxy — silently, on a path that otherwise looks
            # correct. Only the ANTHROPIC_* routing keys are overridden; the
            # caller's other env is kept.
            # A per-session scoped token authenticates the container to the
            # metering proxy (see subscription_provider): the proxy verifies it
            # before spending the subscription, so the proxy can be exposed to a
            # machine network without the public placeholder becoming a way in.
            sub = provider_env.subscription_provider(
                ca_path="/etc/cheese/proxy-ca.pem",
                project_id=str(project_id),
                topic_id=str(topic_id),
                # Session-length TTL, not the 1h default: this token is the
                # container's CLAUDE_CODE_OAUTH_TOKEN, read ONCE at claude start
                # and never hot-refreshed (see ContainerSubscription — env is
                # read at process start; the tmux session is reused across turns).
                # A 1h token expires under the still-running process and the
                # metering proxy then 407s every later turn. Same lifetime as the
                # CHEESE_TOKEN minted for the same session.
                session_token=mint_scoped_token(
                    project_id=str(project_id),
                    topic_id=str(topic_id),
                    ttl_s=SESSION_TOKEN_TTL_S,
                ),
            ).env
            # The caller's env is the gateway provider (BASE_URL + model pins),
            # and it is already stripped of those on the room's container (see
            # _room_env). What is left to do here is ADD the subscription keys.
            merged = {**sub}
        else:
            merged = {}
        config_dir = ws.sandbox_session_dir(topic_id)
        merged.update(
            {
                # THE isolation boundary inside a shared box: claude reads AND
                # writes its config — settings.json, .claude.json, the
                # transcripts --resume reads — under CLAUDE_CONFIG_DIR, and never
                # falls back to $HOME/.claude when it is set (verified on the
                # device path, device_launch.py). HOME stays room-wide on
                # purpose: the shared caches, the toolchain and the git identity
                # under it are things a room SHOULD share; only claude's own
                # state must not be.
                "CLAUDE_CONFIG_DIR": config_dir,
                # The session's cwd (`new-session -c`), which is also the trust
                # entry in .claude.json — this topic's own worktree, at its real
                # path under the project-tree mount.
                "CHEESE_WORKDIR": ws.sandbox_topic_workdir(topic_id),
                # Which published port pair is this topic's — 运行环境预览 and
                # 现场终端 both key off it (ws.app_port_for_slot / ttyd_port_for_slot).
                "CHEESE_PORT_SLOT": str(port_slot),
                "CHEESE_APP_PORT": str(ws.app_port_for_slot(port_slot)),
                # 运行环境预览 reaches the app through the backend's reverse
                # proxy, which serves it under THIS sub-path. A dev server that
                # emits root-absolute asset URLs (vite's `/@vite/client`) must be
                # started under it — `vite --base=$CHEESE_APP_BASE` — or those
                # assets miss the container and hit the platform SPA instead.
                "CHEESE_APP_BASE": f"/api/topics/{topic_id}/app/",
                # No SBX_WORKTREE here, deliberately: on the SDK path that name
                # means a HOST path used as a bind-mount source (sandbox/claude-sbx),
                # and this box never had it. The cwd a session actually runs in
                # is CHEESE_WORKDIR above.
                #
                # No CHEESE_API either: it is the same for every session in the
                # box, so it stays in the room-wide container env (`_room_env`,
                # which sets it from `settings.agent_api_base()` — the #528
                # normaliser, not the raw setting).
                "CHEESE_PROJECT": str(project_id),
                "CHEESE_TOPIC": str(topic_id),
                # Which 分身 this session is (分身独立身份) — the same identity
                # its scoped CHEESE_TOKEN carries, never the shared account.
                "CHEESE_AUTHOR": topic_agent_handle(topic_id),
                "CHEESE_TOKEN": token,
                # Where the baked cheese-hook script forwards hook payloads.
                "CHEESE_HOOK_URL": f"{_hook_base()}/sandbox/hooks/{topic_id}",
                # Durable event WAL the forwarder appends to BEFORE its curl, so
                # 现场 events survive a backend restart mid-turn; the backend
                # reconciles it via ws.spool_dir. Inside this topic's config dir
                # (→ host session_dir/cheese-spool), so the backend can read it.
                "CHEESE_HOOK_SPOOL": f"{config_dir}/cheese-spool",
                # `cheese await`'s output logs, in the same place and for the
                # same reason: await is FOR commands that run long enough to be
                # caught by a container rebuild, and a rebuild used to take the
                # whole log with it (→ host session_dir/cheese-await, readable by
                # the backend via ws.await_log_dir). Not the worktree — a build
                # log has no business in a commit.
                "CHEESE_AWAIT_LOGS": f"{config_dir}/cheese-await",
            }
        )
        if memory_scope:
            merged["CHEESE_MEMORY_SCOPE"] = memory_scope
        if owner:
            merged["CHEESE_OWNER"] = owner
        if turn_id:
            merged["CHEESE_TURN"] = str(turn_id)
        return merged

    async def _room_id(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> uuid.UUID:
        """Which room's box this topic runs in — itself when it IS a room.

        Rooms are `root`/`topic`; a `task` belongs to the room it was split out
        of. Tasks do not nest (topic/services.py `_child_kind`), so one hop up is
        the whole walk — but the loop follows `parent_id` until it reaches a room
        anyway, because the legacy `subtopic` kind predates that rule and rows
        with it still exist.

        The answer is recorded with `ws.bind_room` so the sync, DB-free side of
        the workspace layer (`docker port` lookups on the request path) can find
        the box too. Any failure falls back to the topic's own id, which is the
        one-box-per-topic behaviour — a degraded answer, never a wrong box.
        """
        if not settings.sandbox_share_room_container:
            return topic_id
        try:
            from sqlalchemy import select

            from app.domain.topic.models import Topic, TopicKind

            factory = self._session_factory
            if factory is None:
                from app.core.db import async_session_factory

                factory = async_session_factory
            rooms = (TopicKind.root, TopicKind.topic)
            async with factory() as session:
                current = topic_id
                for _ in range(_MAX_ROOM_WALK):
                    row = (
                        await session.execute(
                            select(Topic.kind, Topic.parent_id, Topic.project_id).where(
                                Topic.id == current
                            )
                        )
                    ).first()
                    if row is None or row.project_id != project_id:
                        return topic_id
                    if row.kind in rooms or row.parent_id is None:
                        ws.bind_room(topic_id, current)
                        return current
                    current = row.parent_id
        except Exception:  # noqa: BLE001 — never fail a turn over box placement
            logger.warning("could not resolve the room of %s", topic_id, exc_info=True)
        return topic_id

    async def precheck(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> object:
        """Fail fast when Docker is absent — BEFORE the runtime claims the
        topic's hook queue. ``topic_id`` is unused here (a local box has no
        per-topic device affinity)."""
        if not self.available():
            raise ScreenSetupError(self.needs_topic_message)
        return None

    async def ensure_ready(
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
        system_prompt: str,
        precheck: object,
    ) -> TmuxScreen:
        """Bring up (or reuse) the topic's tmux `claude` and wait for the `❯`
        input box; return its screen (the room's box + this topic's session).
        Raises ScreenSetupError on setup failure / not-ready."""
        try:
            room_id = await self._room_id(project_id, topic_id)
            # session_dir() seeds the cheese skill into this topic's config dir;
            # sessions_root() is the mount source covering the whole room.
            session_dir = str(ws.session_dir(project_id, topic_id))
            sessions_root = str(ws.sessions_root(project_id))
            # Ensure the topic's own worktree exists before anything is pointed
            # at it — the box's mount is the project tree, not this directory,
            # so a missing worktree would show up as an empty cwd rather than a
            # failed mount.
            ws.topic_worktree(project_id, topic_id)
            container = await self._ensure_container(
                topic_id,
                room_id,
                self._room_env(
                    project_id=project_id,
                    room_id=room_id,
                    sessions_root=sessions_root,
                    env=env,
                ),
            )
            screen = TmuxScreen(container, ws.tmux_session_name(topic_id))
            topic_env = self._topic_env(
                project_id=project_id,
                topic_id=topic_id,
                token=token,
                port_slot=await self._allocate_port_slot(screen),
                env=env,
                memory_scope=memory_scope,
                owner=owner,
                turn_id=turn_id,
            )
            # Seed hooks + skip-disclaimer settings before the session starts
            # (only read at session creation), then bring the session up.
            self._write_session_settings(session_dir, topic_env["CHEESE_WORKDIR"])
            # Always (re)write the system prompt, even when the session already
            # exists: a running claude keeps the prompt it launched with, and
            # this write is what the NEXT fresh session picks up.
            self._write_system_prompt(session_dir, system_prompt)
            await self._ensure_session(
                screen,
                model,
                session_env=topic_env,
                resume_session_id=resume_session_id,
                session_dir=session_dir,
                system_prompt=system_prompt,
            )
            # _wait_ready inside the wrap too: its docker exec can itself fail
            # (docker binary vanishing mid-turn) — that must surface as a clean
            # error result, not a raw exception (review finding).
            ready = await self._wait_ready(screen)
        except Exception as exc:  # noqa: BLE001 — any setup failure ends the turn
            raise ScreenSetupError(f"tmux 后端启动失败：{exc}") from exc
        if not ready:
            raise ScreenSetupError("tmux 会话未就绪（未等到输入框），已放弃本轮")
        return screen

    def _write_session_settings(self, session_dir: str, workdir: str) -> None:
        """Seed the topic's CLAUDE_CONFIG_DIR: the hooks settings, and the
        first-launch gates.

        Idempotent — the hook command is static (the per-topic URL + token live
        in the tmux session env, not the file).

        The GATES have to be written here, and this is not belt-and-braces. The
        sandbox image bakes them at `/home/node/.claude.json` (tmux.Dockerfile),
        which worked while claude read its config from $HOME — but with
        CLAUDE_CONFIG_DIR set, claude reads AND writes `.claude.json` under THAT
        directory and never falls back to $HOME (verified on the device path,
        device_launch.py). Without this the onboarding/trust dialog eats the
        first prompt, the pane never reaches `❯`, and every turn dies at the
        45-second ready handshake with nothing saying why.

        The trust entry names this topic's OWN cwd. The baked file trusts
        `/work`, a remap that no longer exists — another thing a per-topic file
        can get right and a baked one cannot."""
        target = Path(session_dir) / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        _rewrite(
            target,
            json.dumps(hooks_settings(), ensure_ascii=False),
            mode=0o666,
        )
        _rewrite(
            Path(session_dir) / ".claude.json",
            json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "autoUpdates": False,
                    # Legacy fallback, still honored; it migrates to
                    # skipDangerousModePermissionPrompt on first run.
                    "bypassPermissionsModeAccepted": True,
                    "projects": {
                        workdir: {
                            "hasTrustDialogAccepted": True,
                            "hasCompletedProjectOnboarding": True,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            mode=0o666,
        )
        # Login is via CLAUDE_CODE_OAUTH_TOKEN in the container env (see
        # subscription_provider), NOT a .credentials.json — the file gets the
        # local validation the env var skips, and rejected the placeholder as
        # "Not logged in". So nothing credential-shaped is planted here.

    def _write_system_prompt(self, session_dir: str, system_prompt: str) -> None:
        """Write the platform's system prompt into the session mount, where the
        launch line's ``--append-system-prompt-file`` points. An empty prompt
        still writes (an empty file), so a topic whose prompt was withdrawn does
        not keep serving a stale one to its next fresh session."""
        target = Path(session_dir) / _SYSTEM_PROMPT_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        _rewrite(target, system_prompt, mode=0o644)

    def checkpoint(self, project_id: uuid.UUID, topic_id: uuid.UUID) -> None:
        """Snapshot the interactive session's native edits into version history
        Best-effort — never fail a turn.
        Held while a `cheese await` command is still writing the worktree."""
        if not self.available():
            return
        awaited_tasks.checkpoint_worktree(project_id, topic_id)
