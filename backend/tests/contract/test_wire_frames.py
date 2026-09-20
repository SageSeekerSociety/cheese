"""The Python half of the executor wire contract.

`backend/tests/fixtures/wire/*.json` is one committed frame per file, and
`cli/internal/link/frames_test.go` reads the same files. Each fixture carries
the frame as it goes over the socket plus what it means, so both languages can
be asked the two questions that matter at a seam: does the side that *writes*
this frame produce these exact bytes, and does the side that *reads* it get that
meaning back out.

Here that is: `execution.call` is generated (the backend writes it) and the
`execution.*` answers are parsed (the machine writes them). Over in Go it is the
mirror image. Neither file can be made green by editing the other, which is the
whole point — a field renamed on one side turns one of the two red.

No process, no socket, no device: this reads four files.
"""

import base64

import pytest

from app.domain.agent import device_link
from tests.support import wire

FIXTURES = wire.fixtures()
IDS = [f"{f['type']}-{i}" for i, f in enumerate(FIXTURES)]


def _only(frame_type: str) -> dict:
    matches = [f for f in FIXTURES if f["type"] == frame_type]
    assert len(matches) == 1, f"{frame_type}: {len(matches)} fixtures"
    return matches[0]


def test_all_three_executor_frames_are_pinned() -> None:
    """A frame with no fixture is a frame the two languages agree on by luck."""
    assert {f["type"] for f in FIXTURES} >= {
        "execution.call",
        "execution.data",
        "execution.result",
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=IDS)
def test_a_fixture_says_who_writes_the_frame_and_why(fixture) -> None:
    assert set(fixture) == {"type", "origin", "why", "frame", "meaning"}
    assert fixture["origin"] in {"server", "device"}
    assert fixture["frame"]["t"] == fixture["type"]
    assert fixture["why"].strip()
    if "data" in fixture["frame"]:
        base64.b64decode(fixture["frame"]["data"], validate=True)


def test_the_backend_writes_the_execution_call_in_the_fixture() -> None:
    fixture = _only("execution.call")
    meaning = fixture["meaning"]
    assert (
        device_link.execution_call(
            call_id=meaning["call_id"],
            state=meaning["state"],
            method=meaning["method"],
            params=meaning["params"],
            timeout=meaning["timeout_seconds"],
        )
        == fixture["frame"]
    )


def test_the_call_reaches_the_executor_socket_as_a_method_and_params() -> None:
    """`stdin` is not an encoding detail — the connector writes it into the
    socket verbatim, so what the runner reads is fixed here."""
    fixture = _only("execution.call")
    meaning = fixture["meaning"]
    call = wire.ExecutionCall.parse(fixture["frame"])
    assert call.path == meaning["state"]
    assert call.timeout == meaning["timeout_seconds"]
    assert call.request == {"method": meaning["method"], "params": meaning["params"]}


def test_a_data_frame_carries_the_executors_bytes_unchanged() -> None:
    fixture = _only("execution.data")
    frame = fixture["frame"]
    payload = fixture["meaning"]["payload"]

    # Parsed the way the hub parses it: the answer comes back byte for byte,
    # non-ASCII included.
    parsed = device_link.LinkMsg.parse(frame)
    assert parsed.id == fixture["meaning"]["call_id"]
    assert parsed.decoded_data() == payload.encode()

    # And written the way a device double writes it.
    assert wire.execution_data(parsed.id, payload.encode()) == frame


@pytest.mark.parametrize(
    "fixture",
    [f for f in FIXTURES if f["type"] == "execution.result"],
    ids=lambda f: "error" if f["meaning"]["error"] else "clean",
)
def test_a_result_frame_carries_the_machines_own_words(fixture) -> None:
    frame = fixture["frame"]
    error = fixture["meaning"]["error"]

    parsed = device_link.LinkMsg.parse(frame)
    assert parsed.id == fixture["meaning"]["call_id"]
    assert parsed.error == error

    # An absent `error` and an empty one are different frames: Go's `omitempty`
    # never sends the key, so neither does a double.
    assert wire.execution_result(parsed.id, error) == frame
    assert ("error" in frame) is bool(error)
