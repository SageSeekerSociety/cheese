"""Clone a topic's Claude conversation into another topic (transcript-fork).

This is NOT 分身 (that is a *fresh* subagent session + task brief — see
fusion-design §6 and TopicService.split_to_subtopic). Clone is the other thing:
a **deep copy of one agent's full conversation state** onto a new session — the
「复制自」template, or "fork the current state and explore in parallel" (Claude
Code's ``/fork``).

Mechanics (borrowed from the device-backend reference ``clone.py`` — the pure
``slug_for`` / ``fork_transcript`` logic is copied verbatim): Claude stores a
conversation as one append-only transcript at
``$HOME/.claude/projects/<slug(cwd)>/<sessionId>.jsonl``. That single file IS the
conversation. To clone it we read the source transcript, mint a NEW session id
and rewrite every ``sessionId`` reference (a fork, so the copy can never
interleave with the original), then write it under the TARGET session's slug. The
target topic then resumes it with ``claude --resume <newSessionId>``.

Why host-side file ops (not ``docker cp`` / base64-over-exec like the reference):
the tmux backend mounts each topic's persistent ``~/.claude`` FROM a host path
(``ws.session_dir``) INTO the container (``-v {session_dir}:/home/node/.claude``).
So a transcript the container wrote at ``~/.claude/projects/-work/<id>.jsonl`` is
literally the host file ``{session_dir}/projects/-work/<id>.jsonl``. Reading and
writing those host files is exactly equivalent to reaching into the container —
no ``docker cp``, no chunked base64 — and it works even while the container is
down. (The reference needed exec RPC only because its agents live on remote
*devices* with no shared filesystem; ours are local containers over a bind mount.)
"""

import uuid
from pathlib import Path

# The cwd the tmux container ran the agent with was ``/work`` before the
# project-tree mount (now it is the topic's real path under /topics — see
# ws.sandbox_topic_workdir). Transcripts written back then still sit under this
# cwd's slug, which is why reads go through find_transcript below.
LEGACY_CONTAINER_CWD = "/work"


def slug_for(cwd: str) -> str:
    """Claude's per-project directory name: the absolute cwd with every character
    that is not a letter or digit replaced by ``-`` (e.g. ``/topics/topic_ab12``
    → ``-topics-topic-ab12``). Underscores are replaced too — verified against
    live ``~/.claude/projects`` entries; the reference clone.py's narrower
    ``/`` + ``.`` rule only agreed with Claude by accident of ``/work`` containing
    neither. ``--resume`` resolves the transcript by this slug, so a clone must
    be written under the slug of the TARGET topic's cwd."""
    return "".join(c if c.isalnum() else "-" for c in cwd)


def transcript_file(session_dir: Path, session_id: str, *, cwd: str) -> Path:
    """Host path of a session's transcript inside a topic's ``~/.claude`` mount.

    ``session_dir`` is the host dir bind-mounted at the container's ``$HOME/.claude``
    (``ws.session_dir``); Claude writes transcripts under
    ``<HOME>/.claude/projects/<slug(cwd)>/<sessionId>.jsonl``, so on the host that
    is ``<session_dir>/projects/<slug(cwd)>/<sessionId>.jsonl``."""
    return session_dir / "projects" / slug_for(cwd) / f"{session_id}.jsonl"


def find_transcript(session_dir: Path, session_id: str) -> Path | None:
    """Locate a session's transcript under ANY project slug, or None.

    Session ids are UUIDs, so the filename alone identifies the session — the
    slug directory only matters when WRITING (Claude looks a ``--resume`` up
    under its current cwd's slug). Reading by glob makes every consumer immune
    to cwd changes: transcripts written under the legacy ``/work`` slug and
    under per-topic workdirs are all found the same way."""
    matches = sorted(session_dir.glob(f"projects/*/{session_id}.jsonl"))
    return matches[0] if matches else None


def fork_transcript(data: bytes, old_session_id: str, new_session_id: str) -> bytes:
    """Rewrite every ``sessionId`` occurrence so the fork carries a fresh id and
    cannot interleave with the original. Session ids are UUIDs — unique enough
    that a blind byte replace is safe (they never appear as substrings of
    unrelated content). Copied from the reference clone.py.

    Credentials / OAuth tokens are never touched: only the transcript bytes are
    rewritten, and only the session id within them."""
    return data.replace(old_session_id.encode(), new_session_id.encode())


def clone_transcript_files(
    *,
    source_session_dir: Path,
    source_session_id: str,
    target_session_dir: Path,
    new_session_id: str,
    target_cwd: str,
) -> None:
    """Copy + fork the transcript file from one topic's session mount to another.

    Reads the source transcript (found under whatever slug it was written with —
    see find_transcript), rewrites its session id, and writes it under the slug
    of ``target_cwd`` — the cwd the TARGET topic's container will run with,
    because that is where Claude's ``--resume`` will look (creating the
    ``projects/<slug>`` dirs, world-writable so the container's non-root ``node``
    user can keep appending to it). Raises ``FileNotFoundError`` when the source
    transcript is missing or empty (the source agent has not produced a session
    yet)."""
    src = find_transcript(source_session_dir, source_session_id)
    data = src.read_bytes() if src is not None and src.is_file() else b""
    if not data:
        raise FileNotFoundError(
            f"source transcript missing or empty: {source_session_id} "
            f"under {source_session_dir}"
        )
    forked = fork_transcript(data, source_session_id, new_session_id)
    dst = transcript_file(target_session_dir, new_session_id, cwd=target_cwd)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(forked)
    # World-writable so the container's `node` user can append to the resumed
    # session (mirrors ws.session_dir's own chmod policy on the mount).
    dst.chmod(0o666)


def mint_session_id() -> str:
    """A fresh Claude session id (a UUID, matching Claude Code's own format)."""
    return str(uuid.uuid4())
