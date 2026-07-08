"""Cross-machine agent cloning: fork one agent's Claude conversation onto another
device (the「复制自」template when creating an agent).

The whole transfer rides the device ``exec`` RPC (``DeviceHub.exec``) — no ``cli/``
change. Claude stores a conversation as a single append-only transcript at
``~/.claude/projects/<slug(cwd)>/<sessionId>.jsonl``; that one file is the core of a
faithful copy. We read it off the source device, mint a NEW session id and rewrite the
transcript's ``sessionId`` references (a fork — so the copy never collides with the
original), then write it under the target device's cwd-slug. The caller then launches
``claude --resume <newSessionId>`` there.

Chunked because ``exec`` caps each stream at ~1 MiB (see host.go ``execMaxOut``): we
read/write the file in sub-cap base64 chunks. Credentials, machine identity and OAuth
tokens are never touched — only the transcript travels.
"""

import base64
import shlex
from typing import Any, Protocol

from app.core.errors import PreconditionFailedError

# Well under host.go's 1 MiB per-stream exec cap once base64-inflated (~4/3×).
_CHUNK = 512 * 1024


class _Exec(Protocol):
    async def exec(
        self,
        device_id: str,
        argv: list[str],
        *,
        cwd: str | None = ...,
        env: dict[str, str] | None = ...,
        timeout: float = ...,
        stdin: str | None = ...,
    ) -> dict[str, Any]: ...


def slug_for(cwd: str) -> str:
    """Claude's per-project directory name: the absolute cwd with every ``/`` and ``.``
    replaced by ``-`` (e.g. ``/home/u/repo/App`` → ``-home-u-repo-App``). Resume resolves
    the transcript by this slug, so a clone must be written under the TARGET cwd's slug."""
    return "".join("-" if c in "/." else c for c in cwd)


def transcript_path(cwd: str, session_id: str) -> str:
    """The transcript file for a session under a given cwd (``$HOME`` resolved on-device)."""
    return f"$HOME/.claude/projects/{slug_for(cwd)}/{session_id}.jsonl"


def fork_transcript(data: bytes, old_session_id: str, new_session_id: str) -> bytes:
    """Rewrite every ``sessionId`` occurrence so the fork carries a fresh id and cannot
    interleave with the original. UUIDs are unique enough that a blind byte replace is
    safe (they never appear as substrings of unrelated content)."""
    return data.replace(old_session_id.encode(), new_session_id.encode())


async def _exec_ok(
    hub: _Exec, device_id: str, script: str, *, stdin: str | None = None, timeout: float = 120
) -> dict[str, Any]:
    r = await hub.exec(device_id, ["bash", "-lc", script], stdin=stdin, timeout=timeout)
    if r.get("exit") != 0:
        raise PreconditionFailedError(
            f"clone: remote command failed (exit {r.get('exit')}): {str(r.get('stderr', ''))[:300]}"
        )
    return r


async def _decode_flag(hub: _Exec, device_id: str) -> str:
    """Which flag this device's ``base64`` uses to DECODE — GNU coreutils takes ``-d``,
    BSD/macOS takes ``-D``. Detected once so every chunk write is consistent."""
    r = await _exec_ok(
        hub, device_id, "if echo Zm9v | base64 -d >/dev/null 2>&1; then echo -d; else echo -D; fi"
    )
    return "-d" if "-d" in str(r.get("stdout", "")) else "-D"


async def read_remote_file(hub: _Exec, device_id: str, path: str) -> bytes:
    """Read a (possibly large) file off a device in sub-cap base64 chunks. ``path`` may
    contain ``$HOME`` etc. — it is evaluated by the remote shell."""
    r = await _exec_ok(hub, device_id, f"stat -c %s {path} 2>/dev/null || wc -c < {path}")
    size = int(str(r.get("stdout", "0")).strip() or "0")
    out = bytearray()
    idx = 0
    while idx * _CHUNK < size:
        # `base64` encode is the same on GNU/BSD; only the wrapping differs → strip it.
        r = await _exec_ok(
            hub,
            device_id,
            f"dd if={path} bs={_CHUNK} skip={idx} count=1 2>/dev/null | base64 | tr -d '\\n'",
        )
        out.extend(base64.b64decode(str(r.get("stdout", "")).strip()))
        idx += 1
    return bytes(out)


async def write_remote_file(hub: _Exec, device_id: str, path: str, data: bytes) -> None:
    """Write bytes to a file on a device via chunked base64 over exec stdin. Writes to a
    temp file and atomically moves it into place; creates the parent dir."""
    dflag = await _decode_flag(hub, device_id)
    tmp = f"{path}.cheeseclone.tmp"
    # `path` contains $HOME; dirname via shell parameter expansion on the remote.
    await _exec_ok(hub, device_id, f'mkdir -p "$(dirname {path})" && : > {tmp}')
    for i in range(0, len(data), _CHUNK):
        b64 = base64.b64encode(data[i : i + _CHUNK]).decode()
        await _exec_ok(hub, device_id, f"base64 {dflag} >> {tmp}", stdin=b64)
    await _exec_ok(hub, device_id, f"mv {tmp} {path}")


async def clone_session(
    hub: _Exec,
    *,
    source_device_id: str,
    source_cwd: str,
    source_session_id: str,
    target_device_id: str,
    target_cwd: str,
    new_session_id: str,
) -> None:
    """Copy + fork a Claude conversation from one device to another: read the source
    transcript, rewrite its session id to ``new_session_id``, and write it under the
    target cwd's slug. Requires the target cwd to exist on the target device (Claude
    resolves the transcript by that cwd). Both devices must be online."""
    if not target_cwd:
        raise PreconditionFailedError("clone: a target working directory is required")
    # 目标 cwd 不存在也不再阻断克隆：claude 靠 cwd 的 slug 定位 transcript，空目录同样能
    # `--resume` 起来。仓库内容缺失是用户后续自己补的事，不该让克隆失败。所以这里直接
    # `mkdir -p` 把目标目录建出来（幂等，已存在也无妨），再继续写 transcript。
    await _exec_ok(
        hub, target_device_id, f"mkdir -p {shlex.quote(target_cwd)}", timeout=30
    )
    src_path = transcript_path(source_cwd, source_session_id)
    data = await read_remote_file(hub, source_device_id, src_path)
    if not data:
        raise PreconditionFailedError(
            "clone: source transcript is empty or missing (has the source agent run yet?)"
        )
    forked = fork_transcript(data, source_session_id, new_session_id)
    dst_path = transcript_path(target_cwd, new_session_id)
    await write_remote_file(hub, target_device_id, dst_path, forked)
