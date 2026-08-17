"""The ``link.Msg`` wire protocol — the frozen cli's control channel (P3).

The frozen Go cli (``cli/internal/link/link.go``) speaks a single flat JSON union
in both directions over one dial-out WebSocket. This module is the Python side of
that exact contract: ``PROTOCOL_VERSION`` (must equal the cli's ``link.Version``),
typed constructors for every message the server sends down, and ``LinkMsg`` for
parsing what the device sends up. Kept pure + field-for-field with the Go struct
so it is unit-tested without a socket, and a wire-shape drift is caught by a test
rather than in production.

The field names are the Go ``json`` tags verbatim: ``t`` (type), ``sid`` (screen id),
``v`` (version), ``name``/``value`` (variables), ``id``/``args``/``error`` (rpc),
``command``/``env``/``screen``/``cols``/``rows``/``source``/``adopt`` (session),
``data`` (base64 raw screen bytes or one uploaded file), ``path`` (file upload),
and the exec set
``cwd``/``stdin``/``timeout``/``stdout``/``stderr``/``exit``/``truncated``.
"""

import base64
from dataclasses import dataclass
from typing import Any

# Must match cli/internal/link/link.go `Version`. Bump only for a breaking change.
PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class LinkMsg:
    """One parsed inbound ``link.Msg`` from the device. Only the fields a given
    message type uses are meaningful; the rest keep their zero value (as the Go
    side omits them). ``raw`` keeps the original dict for forward-compatibility."""

    t: str
    sid: str = ""
    v: int | None = None
    name: str = ""
    value: Any = None
    id: str = ""
    error: str = ""
    data: str = ""
    path: str = ""
    stdout: str = ""
    stderr: str = ""
    exit: int = 0
    truncated: bool = False
    raw: dict[str, Any] | None = None

    @classmethod
    def parse(cls, m: dict[str, Any]) -> "LinkMsg":
        return cls(
            t=str(m.get("t", "")),
            sid=str(m.get("sid", "")),
            v=m.get("v"),
            name=str(m.get("name", "")),
            value=m.get("value"),
            id=str(m.get("id", "")),
            error=str(m.get("error", "")),
            data=str(m.get("data", "")),
            path=str(m.get("path", "")),
            stdout=str(m.get("stdout", "")),
            stderr=str(m.get("stderr", "")),
            exit=int(m.get("exit", 0) or 0),
            truncated=bool(m.get("truncated", False)),
            raw=m,
        )

    def decoded_data(self) -> bytes:
        """The base64 ``data`` field (raw screen bytes) as bytes; empty on error."""
        if not self.data:
            return b""
        try:
            return base64.b64decode(self.data)
        except (ValueError, TypeError):
            return b""


# --- outbound constructors (server → device) -----------------------------------
# Every dict is a valid link.Msg the frozen cli understands. Optional fields are
# omitted (not sent as null) to match the Go `omitempty` tags.


def welcome() -> dict[str, Any]:
    return {"t": "welcome", "v": PROTOCOL_VERSION}


def session_create(
    *,
    sid: str,
    command: list[str],
    screen_token: str,
    cols: int,
    rows: int,
    source: str | None = None,
    env: dict[str, str] | None = None,
    adopt: bool = False,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "t": "session.create",
        "sid": sid,
        "command": command,
        "screen": screen_token,
        "cols": cols,
        "rows": rows,
    }
    if source is not None:
        msg["source"] = source
    if env:
        msg["env"] = env
    if adopt:
        msg["adopt"] = True
    return msg


def session_close(sid: str) -> dict[str, Any]:
    return {"t": "session.close", "sid": sid}


def script_load(sid: str, source: str) -> dict[str, Any]:
    return {"t": "script.load", "sid": sid, "source": source}


def var_set(sid: str, name: str, value: Any) -> dict[str, Any]:
    return {"t": "var.set", "sid": sid, "name": name, "value": value}


def rpc_call(sid: str, call_id: str, name: str, args: list[Any]) -> dict[str, Any]:
    return {"t": "rpc.call", "sid": sid, "id": call_id, "name": name, "args": args}


def rpc_result(sid: str, call_id: str, value: Any, error: str = "") -> dict[str, Any]:
    return {
        "t": "rpc.result",
        "sid": sid,
        "id": call_id,
        "value": value,
        "error": error,
    }


def file_put(sid: str, file_id: str, path: str, data: bytes) -> dict[str, Any]:
    """Stage one worktree-relative file in a screen's workspace."""
    return {
        "t": "file.put",
        "sid": sid,
        "id": file_id,
        "path": path,
        "data": base64.b64encode(data).decode(),
    }


def screen_subscribe(sid: str, cols: int, rows: int) -> dict[str, Any]:
    return {"t": "screen.subscribe", "sid": sid, "cols": cols, "rows": rows}


def screen_unsubscribe(sid: str) -> dict[str, Any]:
    return {"t": "screen.unsubscribe", "sid": sid}


def screen_input(sid: str, data: bytes) -> dict[str, Any]:
    return {"t": "screen.input", "sid": sid, "data": base64.b64encode(data).decode()}


def screen_resize(sid: str, cols: int, rows: int) -> dict[str, Any]:
    return {"t": "screen.resize", "sid": sid, "cols": cols, "rows": rows}


def exec_cmd(
    *,
    exec_id: str,
    command: list[str],
    timeout: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "t": "exec",
        "id": exec_id,
        "command": command,
        "timeout": timeout,
    }
    if cwd:
        msg["cwd"] = cwd
    if env:
        msg["env"] = env
    if stdin:
        msg["stdin"] = stdin
    return msg


def exec_cancel(exec_id: str) -> dict[str, Any]:
    return {"t": "exec.cancel", "id": exec_id}


def update() -> dict[str, Any]:
    return {"t": "update"}
