"""The helper's patience window (#551 止血): a backend swap must read as one
slow request, not as ECONNREFUSED. Only ABSENCE is ridden out — an endpoint
that answered and said no (bad token, missing route) still fails fast, because
a claude waiting on a CONNECT that can never succeed looks like a stalled
model."""

import pytest

from app.domain.agent import machine_tunnel
from app.domain.agent.machine_tunnel import TunnelError

_SENTINEL = object()


def _patient(monkeypatch, outcomes: list):
    """Drive _open_with_patience over a scripted open_tunnel."""
    calls: list[int] = []

    def fake_open(url, token, *, ca_path=None, insecure=False):
        calls.append(1)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(machine_tunnel, "open_tunnel", fake_open)
    return calls


def test_connection_refused_is_retried_until_the_backend_returns(monkeypatch):
    calls = _patient(
        monkeypatch, [ConnectionRefusedError(), ConnectionRefusedError(), _SENTINEL]
    )
    ws, secret = machine_tunnel._open_with_patience(
        "ws://x/llm/tunnel",
        "tok",
        ca_path=None,
        insecure=False,
        window_s=5.0,
        start_delay_s=0.01,
    )
    assert ws is _SENTINEL
    assert secret == "tok"
    assert len(calls) == 3


def test_a_gateway_502_is_retried(monkeypatch):
    calls = _patient(
        monkeypatch,
        [
            TunnelError("upgrade refused: HTTP/1.1 502 Bad Gateway", retryable=True),
            _SENTINEL,
        ],
    )
    ws, _secret = machine_tunnel._open_with_patience(
        "ws://x/llm/tunnel",
        "tok",
        ca_path=None,
        insecure=False,
        window_s=5.0,
        start_delay_s=0.01,
    )
    assert ws is _SENTINEL
    assert len(calls) == 2


def test_a_refused_upgrade_fails_fast(monkeypatch):
    calls = _patient(
        monkeypatch, [TunnelError("upgrade refused: HTTP/1.1 403 Forbidden")]
    )
    with pytest.raises(TunnelError):
        machine_tunnel._open_with_patience(
            "ws://x/llm/tunnel",
            "tok",
            ca_path=None,
            insecure=False,
            window_s=5.0,
            start_delay_s=0.01,
        )
    assert len(calls) == 1  # answered-and-refused is not ridden out


def test_the_window_is_bounded(monkeypatch):
    calls = _patient(monkeypatch, [ConnectionRefusedError()])
    with pytest.raises(OSError):
        machine_tunnel._open_with_patience(
            "ws://x/llm/tunnel",
            "tok",
            ca_path=None,
            insecure=False,
            window_s=0.05,
            start_delay_s=0.02,
        )
    assert len(calls) >= 1


def test_open_tunnel_marks_gateway_absence_retryable():
    err = TunnelError("upgrade refused: HTTP/1.1 503 Service Unavailable")
    assert machine_tunnel._is_retryable(err) is False  # unmarked → conservative
    assert machine_tunnel._is_retryable(TunnelError("x", retryable=True)) is True
    assert machine_tunnel._is_retryable(ConnectionResetError()) is True
