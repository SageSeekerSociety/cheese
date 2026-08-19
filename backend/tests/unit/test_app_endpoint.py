"""运行环境预览 port lookup — which container is asked, and what a browser is told.

The preview was 100% dead for every tmux-backed topic because the lookup went to
the SDK box (`cheesex-sbx-*`) while the app port is published by the tmux box
(`cheesex-tmux-*`). No container needed to pin that: the `docker port` call is
behind one seam.
"""

import uuid

from app.domain.workspace import service as ws


def _seam(monkeypatch, mapping: dict[str, str]) -> list[str]:
    """Replace the `docker port` call with a table, recording who was asked."""
    asked: list[str] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: True)

    def _published(container: str, _port: int) -> str | None:
        asked.append(container)
        return mapping.get(container)

    monkeypatch.setattr(ws, "published_endpoint", _published)
    return asked


def test_app_endpoint_finds_the_tmux_box(monkeypatch):
    tid = uuid.uuid4()
    asked = _seam(monkeypatch, {ws.tmux_container_name(tid): "127.0.0.1:55007"})
    assert ws.app_endpoint(tid) == "127.0.0.1:55007"
    assert ws.tmux_container_name(tid) in asked


def test_app_endpoint_still_finds_the_sdk_box(monkeypatch):
    """Both backends leave a box; a topic that ran on the SDK one must still work."""
    tid = uuid.uuid4()
    _seam(monkeypatch, {ws.container_name(tid): "127.0.0.1:55008"})
    assert ws.app_endpoint(tid) == "127.0.0.1:55008"


def test_app_endpoint_is_none_when_no_box_publishes_it(monkeypatch):
    tid = uuid.uuid4()
    _seam(monkeypatch, {})
    assert ws.app_endpoint(tid) is None


def test_only_a_local_container_can_host_an_app_preview():
    """`docker port` runs on the BACKEND's host, so a topic whose turn runs on
    someone's own machine (`device`) or a leased Cloud machine (`cloud`) has
    nothing here to look up — that is a different state from "the container
    died", and the panel's copy depends on telling them apart."""
    from app.domain.agent.compute import app_preview_reachable
    from app.domain.agent.tmux_provider import TmuxHooksProvider

    assert app_preview_reachable(TmuxHooksProvider.name) is True
    assert app_preview_reachable("device") is False
    assert app_preview_reachable("cloud") is False
    # Unpinned topic → whatever this deployment defaults to, not a blanket yes.
    from app.domain.agent.market import compute_default_name

    assert app_preview_reachable(None) is app_preview_reachable(compute_default_name())


def test_no_host_loopback_url_is_handed_out_any_more(monkeypatch):
    """The old `app_preview_url` returned `http://127.0.0.1:<host-port>`, which
    resolves to the VIEWER's own machine — a white frame for every remote user.
    Nothing may reintroduce a host-bound address as a browser-facing URL."""
    assert not hasattr(ws, "app_preview_url")
