"""link.Msg wire protocol: constructors + parse round-trip (the cli's contract)."""

import base64

from app.domain.agent import device_link
from app.domain.agent.device_link import PROTOCOL_VERSION, LinkMsg


def test_welcome_and_version():
    assert device_link.welcome() == {"t": "welcome", "v": PROTOCOL_VERSION}
    assert PROTOCOL_VERSION == 1  # must match cli/internal/link/link.go Version


def test_session_create_omits_empty_optionals():
    msg = device_link.session_create(
        sid="s1", command=["claude"], screen_token="tok", cols=120, rows=32
    )
    assert msg == {
        "t": "session.create",
        "sid": "s1",
        "command": ["claude"],
        "screen": "tok",
        "cols": 120,
        "rows": 32,
    }
    # env / adopt only present when set (matches Go omitempty).
    assert "env" not in msg and "adopt" not in msg


def test_session_create_with_optionals():
    msg = device_link.session_create(
        sid="s1",
        command=["claude"],
        screen_token="tok",
        cols=1,
        rows=1,
        env={"A": "B"},
        adopt=True,
    )
    assert msg["env"] == {"A": "B"} and msg["adopt"] is True


def test_screen_input_base64_encodes():
    msg = device_link.screen_input("s1", b"\x1b[Ahi")
    assert msg["t"] == "screen.input"
    assert base64.b64decode(msg["data"]) == b"\x1b[Ahi"


def test_rpc_and_exec_constructors():
    assert device_link.rpc_call("s", "id1", "prompt", ["hi"]) == {
        "t": "rpc.call",
        "sid": "s",
        "id": "id1",
        "name": "prompt",
        "args": ["hi"],
    }
    ex = device_link.exec_cmd(exec_id="e1", command=["ls"], timeout=5, cwd="/w")
    assert ex["t"] == "exec" and ex["cwd"] == "/w" and ex["timeout"] == 5


def test_file_put_carries_one_binary_file_as_base64():
    raw = b"\x89PNG\r\n\x1a\n\x00\xff"
    msg = device_link.file_put("s1", "f1", "uploads/img-a.png", raw)
    assert msg == {
        "t": "file.put",
        "sid": "s1",
        "id": "f1",
        "path": "uploads/img-a.png",
        "data": base64.b64encode(raw).decode(),
    }


def test_parse_inbound_and_decoded_data():
    m = LinkMsg.parse(
        {
            "t": "screen.data",
            "sid": "s1",
            "data": base64.b64encode(b"hello").decode(),
        }
    )
    assert m.t == "screen.data" and m.sid == "s1"
    assert m.decoded_data() == b"hello"
    assert m.raw is not None and m.raw["t"] == "screen.data"


def test_parse_tolerates_missing_fields():
    m = LinkMsg.parse({"t": "hello", "v": 1})
    assert m.t == "hello" and m.v == 1 and m.sid == "" and m.decoded_data() == b""


def test_parse_hello_carries_which_binary_is_speaking():
    m = LinkMsg.parse(
        {"t": "hello", "v": 1, "build": "a" * 64, "target": "linux-amd64"}
    )
    assert m.build == "a" * 64 and m.target == "linux-amd64"


def test_parse_hello_from_a_connector_too_old_to_say():
    """The version alone cannot distinguish it from a current build — which is
    exactly why the server treats the silence as old."""
    m = LinkMsg.parse({"t": "hello", "v": 1})
    assert m.build == "" and m.target == ""


def test_update_frame():
    assert device_link.update() == {"t": "update"}
