"""Freeing a topic's compute. `stop_topic_container` (the idle reaper's tool) must
reap BOTH backends' boxes — the old code only removed the SDK container, so every
tmux-backed topic leaked its `cheesex-tmux-*` container forever. The accept path
no longer calls it at all; see the second half of this file."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.workspace import service as ws


def test_sdk_and_tmux_container_names_are_distinct():
    tid = uuid.uuid4()
    assert ws.container_name(tid) == f"cheesex-sbx-{tid.hex[:12]}"
    assert ws.tmux_container_name(tid) == f"cheesex-tmux-{tid.hex[:12]}"
    assert ws.container_name(tid) != ws.tmux_container_name(tid)


def test_stop_topic_container_reaps_both_backends(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(ws.subprocess, "run", lambda argv, **kw: calls.append(argv))

    tid = uuid.uuid4()
    ws.stop_topic_container(tid)

    removed = {argv[-1] for argv in calls if argv[:3] == ["docker", "rm", "-f"]}
    assert ws.container_name(tid) in removed  # cheesex-sbx-… (SDK box)
    assert ws.tmux_container_name(tid) in removed  # cheesex-tmux-… (tmux box)


def test_stop_topic_container_noop_without_docker(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ws, "sandbox_available", lambda: False)
    monkeypatch.setattr(ws.subprocess, "run", lambda argv, **kw: calls.append(argv))
    ws.stop_topic_container(uuid.uuid4())
    assert calls == []  # no docker → never shells out


# 交付时释放算力，从 2026-08-17 起只包括**计费的**那一件 (#442 decision 1)：
# 云 VM 按小时烧钱、而且没有 reaper，所以它必须在这里回收；容器和设备屏是可重建
# 的工作面、各自有闲置回收器，而话题合并之后还要接着用它们，所以不再动。


@pytest.mark.anyio
async def test_delivery_releases_the_cloud_vm_only(monkeypatch):
    """交付释放计费算力，但不拆工作面：容器不删、设备屏不关。

    合并不再等于话题结束，所以拆掉正在用的箱子是纯粹的损失——重建的容器会丢掉
    里面装好的一切（jj、procps、测试要用的 git identity）。
    """
    from app.domain.agent import device_provider
    from app.domain.review.services import AcceptService

    containers: list[uuid.UUID] = []
    screens: list[tuple] = []
    monkeypatch.setattr(ws, "stop_topic_container", containers.append)

    async def fake_release(project_id, topic_id, **kw):
        screens.append((project_id, topic_id))

    monkeypatch.setattr(device_provider, "release_topic_screen", fake_release)

    pid, tid = uuid.uuid4(), uuid.uuid4()
    topic = SimpleNamespace(id=tid, project_id=pid)
    # The method needs no DB — exercise it on a bare instance.
    svc = AcceptService.__new__(AcceptService)
    svc._machines = AsyncMock()
    await svc._release_billed_compute(topic)

    svc._machines.release_topic_machine.assert_awaited_once_with(tid)
    assert containers == []  # 沙箱容器留着
    assert screens == []  # 设备屏也留着


@pytest.mark.anyio
async def test_delivery_propagates_cloud_deletion_failure():
    """云 VM 的释放不是 best-effort：MicroCloud 没接受删除，采纳不能报成功——
    否则一个计费泄漏会永久藏在"已交付"后面，而它没有任何 reaper 兜底。"""
    from app.domain.review.services import AcceptService

    topic = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    svc = AcceptService.__new__(AcceptService)
    svc._machines = AsyncMock()
    svc._machines.release_topic_machine.side_effect = RuntimeError(
        "MicroCloud refused deletion"
    )

    with pytest.raises(RuntimeError, match="refused deletion"):
        await svc._release_billed_compute(topic)
