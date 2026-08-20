"""施工现场 terminal proxy — backend-gate + status shaping (no container needed).

The proxy only offers a terminal under AGENT_BACKEND=tmux with the topic's
container up; under the SDK backend it must always report unavailable so the
frontend keeps the worklog view.
"""

import uuid

from app.api.routes import terminal as term
from app.core.config import settings


def test_live_endpoint_none_without_a_local_screen(monkeypatch):
    """No published port means no pane to embed — the topic runs elsewhere, or
    its box is down. There is no backend switch in front of this any more: the
    port mapping IS the question."""
    monkeypatch.setattr(term, "ttyd_endpoint", lambda _t: None)
    assert term._live_endpoint(uuid.uuid4()) is None


def test_live_endpoint_resolves_when_the_box_publishes_one(monkeypatch):
    monkeypatch.setattr(term, "ttyd_endpoint", lambda _t: "127.0.0.1:55011")
    assert term._live_endpoint(uuid.uuid4()) == "127.0.0.1:55011"


def test_live_endpoint_none_when_container_down_under_tmux(monkeypatch):
    monkeypatch.setattr(settings, "agent_backend", "tmux")
    monkeypatch.setattr(term, "ttyd_endpoint", lambda _t: None)
    assert term._live_endpoint(uuid.uuid4()) is None
