"""Freeing a topic's compute (on 采纳/archive, via stop_topic_container) must reap
BOTH backends' boxes. The old code only removed the SDK container, so every
tmux-backed topic leaked its `cheesex-tmux-*` container forever."""

import uuid

from app.domain.workspace import service as ws


def test_sdk_and_tmux_container_names_are_distinct():
    tid = uuid.uuid4()
    assert ws.container_name(tid) == f"cheesex-sbx-{tid.hex[:12]}"
    assert ws.tmux_container_name(tid) == f"cheesex-tmux-{tid.hex[:12]}"
    assert ws.container_name(tid) != ws.tmux_container_name(tid)


def test_stop_topic_container_reaps_both_backends(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        ws.subprocess, "run", lambda argv, **kw: calls.append(argv)
    )

    tid = uuid.uuid4()
    ws.stop_topic_container(tid)

    removed = {argv[-1] for argv in calls if argv[:3] == ["docker", "rm", "-f"]}
    assert ws.container_name(tid) in removed  # cheesex-sbx-… (SDK box)
    assert ws.tmux_container_name(tid) in removed  # cheesex-tmux-… (tmux box)


def test_stop_topic_container_noop_without_docker(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: False)
    monkeypatch.setattr(
        ws.subprocess, "run", lambda argv, **kw: calls.append(argv)
    )
    ws.stop_topic_container(uuid.uuid4())
    assert calls == []  # no docker → never shells out
