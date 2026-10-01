"""Fixed requests reach the selected native listener or fail before app bytes."""

import socket

import pytest

from app.domain.agent import preview_tunnel as wire


def test_listener_identity_survives_helper_restart_not_listener_replacement(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    path = tmp_path / "port"
    path.write_text(str(listener.getsockname()[1]))
    source = wire.PortSource(str(path))
    try:
        first = source.instance()
        assert wire.PortSource(str(path)).instance() == first
        source.check(first)
        replacement = socket.socket()
        replacement.bind(("127.0.0.1", 0))
        replacement.listen()
        try:
            path.write_text(str(replacement.getsockname()[1]))
            assert source.instance() != first
            with pytest.raises(wire.PreviewError, match="instance gone"):
                source.check(first)
        finally:
            replacement.close()
    finally:
        listener.close()


def test_same_port_listener_replacement_has_new_identity(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    address = listener.getsockname()
    path = tmp_path / "port"
    path.write_text(str(address[1]))
    source = wire.PortSource(str(path))
    first = source.instance()
    listener.close()
    with socket.socket() as replacement:
        replacement.bind(address)
        replacement.listen()
        assert source.instance() != first
        with pytest.raises(wire.PreviewError, match="instance gone"):
            source.check(first)


@pytest.mark.parametrize("kind", [wire.OP_REQ, wire.OP_WS_OPEN])
def test_fixed_request_after_port_replaced_sends_no_application_bytes(tmp_path, kind):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    path = tmp_path / "port"
    path.write_text(str(listener.getsockname()[1]))
    backend, helper = socket.socketpair()
    backend.settimeout(2)
    source = wire.PortSource(str(path))
    session = wire.Session(helper, source, capabilities={"instance-v1"})
    try:
        session._dispatch(
            kind, 1, wire.encode_meta({"path": "/", "instance": "0" * 64})
        )
        _, data = wire.recv_message(backend)
        op, stream, reason = wire.decode(data)
        assert (op, stream) == (wire.OP_ERR, 1)
        assert b"instance gone" in reason
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        session._stop()
        listener.close()
        backend.close()
        helper.close()


@pytest.mark.parametrize("kind", [wire.OP_REQ, wire.OP_WS_OPEN])
def test_same_port_replacement_during_connect_checked_before_app_bytes(
    tmp_path, monkeypatch, kind
):
    old = socket.socket()
    old.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    old.bind(("127.0.0.1", 0))
    old.listen()
    address = old.getsockname()
    new = socket.socket()
    new.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    path = tmp_path / "port"
    path.write_text(str(address[1]))
    source = wire.PortSource(str(path))
    expected = source.instance()
    native = socket.socket.connect
    accepted = []

    def connect(sock, target):
        native(sock, target)
        app, _ = old.accept()
        accepted.append(app)
        old.close()
        new.bind(address)
        new.listen()

    monkeypatch.setattr(socket.socket, "connect", connect)
    backend, helper = socket.socketpair()
    backend.settimeout(2)
    session = wire.Session(helper, source)
    try:
        session._dispatch(
            kind, 1, wire.encode_meta({"path": "/", "instance": expected})
        )
        _, data = wire.recv_message(backend)
        assert wire.decode(data)[0] == wire.OP_ERR
        assert len(accepted) == 1
        accepted[0].settimeout(2)
        assert accepted[0].recv(1) == b""
        print(
            {
                "kind": kind,
                "captured_port": address[1],
                "old_app_bytes": 0,
                "postconnect": "same-port replacement rejected",
            }
        )
    finally:
        session._stop()
        for app in accepted:
            app.close()
        old.close()
        new.close()
        helper.close()
        backend.close()
