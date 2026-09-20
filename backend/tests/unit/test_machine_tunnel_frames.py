"""Verify client wire frames against the WebSocket masking rules."""

import socket
import struct
from unittest.mock import Mock

import pytest

from app.domain.agent import machine_tunnel


def test_rfc6455_masked_text_example(monkeypatch):
    """RFC 6455 section 5.7 provides this complete masked Hello frame."""
    monkeypatch.setattr(
        machine_tunnel.secrets, "token_bytes", lambda count: bytes.fromhex("37fa213d")
    )
    connection = Mock(spec=socket.socket)
    machine_tunnel.send_frame(connection, b"Hello", opcode=1)
    connection.sendall.assert_called_once_with(bytes.fromhex("818537fa213d7f9f4d5158"))


@pytest.mark.parametrize("size", [0, 1, 3, 4, 125, 126, 65535, 65536, 65537])
@pytest.mark.parametrize(
    "mask", [b"\0\0\0\0", b"\xff\xff\xff\xff", b"\x12\x34\x56\x78"]
)
def test_binary_frames_preserve_payload_and_length(monkeypatch, size, mask):
    monkeypatch.setattr(machine_tunnel.secrets, "token_bytes", lambda count: mask)
    connection = Mock(spec=socket.socket)
    payload = (bytes(range(256)) * ((size + 255) // 256))[:size]
    machine_tunnel.send_frame(connection, payload)
    wire = connection.sendall.call_args.args[0]
    assert wire[0] == 0x82
    assert wire[1] & 0x80
    length = wire[1] & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack("!H", wire[2:4])[0]
        offset = 4
    elif length == 127:
        length = struct.unpack("!Q", wire[2:10])[0]
        offset = 10
    assert length == size
    assert wire[offset : offset + 4] == mask
    body = wire[offset + 4 :]
    assert len(body) == size
    assert bytes(value ^ mask[index % 4] for index, value in enumerate(body)) == payload
