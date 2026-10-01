"""Wire limits reject declared overflow before waiting for its missing body."""

import socket
import struct
import threading

import pytest

from app.domain.agent import preview_tunnel as wire


@pytest.mark.parametrize("fragmented", [False, True])
def test_websocket_limit_rejects_declared_overflow_without_reading_body(fragmented):
    reader, peer = socket.socketpair()
    reader.settimeout(0.2)
    try:
        if fragmented:
            peer.sendall(b"\x02\x03abc")
            header = b"\x80\x7f" + struct.pack("!Q", 1024 * 1024 - 2)
        else:
            header = b"\x82\x7f" + struct.pack("!Q", 1024 * 1024 + 1)
        peer.sendall(header)
        with pytest.raises(wire.PreviewError, match="message too large"):
            wire.recv_message(reader)
    finally:
        reader.close()
        peer.close()


def test_metadata_limit_rejects_header_before_json_decode():
    blob = b'{"padding":"' + b"x" * (64 * 1024) + b'"}'
    payload = struct.pack("!I", len(blob)) + blob
    with pytest.raises(ValueError, match="header too large"):
        wire.decode_meta(payload)


def test_cancelled_stream_does_not_send_data_waiting_for_shared_writer(tmp_path):
    backend, helper = socket.socketpair()
    backend.settimeout(0.1)
    session = wire.Session(helper, wire.PortSource(str(tmp_path / "port")))
    state = wire._StreamState(wire.OP_REQ)
    session._streams[1] = state
    session._write_lock.acquire()
    started = threading.Event()

    def producer():
        started.set()
        session.send(wire.OP_DATA, 1, b"must not arrive")

    worker = threading.Thread(target=producer)
    worker.start()
    assert started.wait(1)
    session._cancel(1)
    session._write_lock.release()
    worker.join(1)
    try:
        assert not worker.is_alive()
        with pytest.raises(TimeoutError):
            backend.recv(1)
    finally:
        helper.close()
        backend.close()


@pytest.mark.parametrize("kind", [wire.OP_REQ, wire.OP_WS_OPEN])
def test_cancel_before_connect_closes_published_socket_and_returns_capacity(
    tmp_path, monkeypatch, kind
):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    port_file = tmp_path / "port"
    port_file.write_text(str(listener.getsockname()[1]))
    backend, helper = socket.socketpair()
    session = wire.Session(helper, wire.PortSource(str(port_file)))
    entered = threading.Event()
    release = threading.Event()
    native = socket.socket.connect

    def gated_connect(sock, address):
        entered.set()
        assert release.wait(2)
        return native(sock, address)

    monkeypatch.setattr(socket.socket, "connect", gated_connect)
    try:
        session._dispatch(kind, 1, wire.encode_meta({"path": "/", "headers": []}))
        assert entered.wait(1)
        state = session._streams[1]
        assert state.socket is not None
        session._dispatch(wire.OP_CLOSE, 1, b"")
        assert state.socket.fileno() == -1
        release.set()
        assert state.done.wait(2)
        state.worker.join(1)
        assert not state.worker.is_alive() and not session._streams
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        release.set()
        session._stop()
        helper.close()
        backend.close()
        listener.close()


def test_cancel_during_request_body_write_closes_native_socket_and_worker(
    tmp_path, monkeypatch
):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    port_file = tmp_path / "port"
    port_file.write_text(str(listener.getsockname()[1]))
    backend, helper = socket.socketpair()
    session = wire.Session(helper, wire.PortSource(str(port_file)))
    entered = threading.Event()
    release = threading.Event()
    native = socket.socket.sendall
    body = b"gated-body"

    def gated_sendall(sock, data, *args, **kwargs):
        if data == body:
            entered.set()
            assert release.wait(2)
        return native(sock, data, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "sendall", gated_sendall)
    app = None
    try:
        session._dispatch(
            wire.OP_REQ,
            1,
            wire.encode_meta(
                {"method": "POST", "path": "/upload", "headers": []}, body
            ),
        )
        app, _ = listener.accept()
        app.settimeout(2)
        assert entered.wait(1)
        head = bytearray()
        while b"\r\n\r\n" not in head:
            head.extend(app.recv(4096))
        assert head.startswith(b"POST /upload HTTP/1.1") and body not in head
        state = session._streams[1]
        session._dispatch(wire.OP_CLOSE, 1, b"")
        assert state.socket.fileno() == -1
        assert app.recv(1) == b""
        release.set()
        assert state.done.wait(2)
        state.worker.join(1)
        assert not state.worker.is_alive() and not session._streams
    finally:
        release.set()
        session._stop()
        if app is not None:
            app.close()
        helper.close()
        backend.close()
        listener.close()
