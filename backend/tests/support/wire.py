"""The executor frames as a test speaks them, and the fixtures both languages read.

The three `execution.*` frames cross a seam: the backend writes
`execution.call` (`app/domain/agent/device_link.py`), the Go connector reads it
and answers with `execution.data` / `execution.result`
(`cli/internal/host/executor.go`). Nothing on either side of that seam imports
the other, so the only thing that can hold them together is a fixture both read
— `backend/tests/fixtures/wire/*.json`, checked by
`tests/contract/test_wire_frames.py` here and by `cli/internal/link/frames_test.go`
there.

This module is the device's half in Python: the frames a double sends *up* and
the reader for the one it receives. It exists so a double never hand-types a
frame again — a hand-typed one drifts silently, and the test that drifted with
it goes on passing. `test_wire_frames.py` asserts these builders against the
committed fixtures, so a double built on them is sending what the connector
sends.
"""

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "wire"


def fixtures() -> list[dict[str, Any]]:
    """Every committed wire fixture, ordered by file name (the Go side's order)."""
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    ]


@dataclass(frozen=True)
class ExecutionCall:
    """An `execution.call` read the way the connector reads it.

    `cli/internal/host/host.go` dispatches on `t` and `cli/internal/host/executor.go`
    uses exactly these four fields; a double that needs more than they carry is
    pretending to be something the machine is not.
    """

    id: str
    path: str
    stdin: str
    timeout: int

    @classmethod
    def parse(cls, frame: dict[str, Any]) -> "ExecutionCall":
        assert frame["t"] == "execution.call", frame["t"]
        return cls(
            id=frame["id"],
            path=frame["path"],
            stdin=frame["stdin"],
            timeout=int(frame.get("timeout", 0)),
        )

    @property
    def request(self) -> dict[str, Any]:
        """The method call the connector writes into the executor socket."""
        return json.loads(self.stdin)


def execution_data(call_id: str, payload: bytes) -> dict[str, Any]:
    """One chunk of the executor's answer, base64 of the raw bytes."""
    return {
        "t": "execution.data",
        "id": call_id,
        "data": base64.b64encode(payload).decode(),
    }


def execution_result(call_id: str, error: str = "") -> dict[str, Any]:
    """The end of one call. `error` is omitted when there is none, as Go omits it."""
    frame: dict[str, Any] = {"t": "execution.result", "id": call_id}
    if error:
        frame["error"] = error
    return frame


class RecordingDevice:
    """A device transport that only records what the server sent down.

    Enough for every test whose question is "what frame went out, and what
    happens when the device answers it"; the answers go back in through
    `DeviceHub.on_device_message` built with the constructors above.
    """

    def __init__(self) -> None:
        self.sent: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send_json(self, message: dict[str, Any]) -> None:
        await self.sent.put(message)

    async def next_call(self, timeout: float = 1) -> ExecutionCall:
        """Wait for the next `execution.call`, skipping the opening `welcome`."""
        while True:
            frame = await asyncio.wait_for(self.sent.get(), timeout)
            if frame["t"] == "execution.call":
                return ExecutionCall.parse(frame)
