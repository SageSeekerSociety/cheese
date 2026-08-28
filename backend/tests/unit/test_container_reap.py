"""Freeing a topic's compute when it is delivered: the billed machine, and
nothing else."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# 交付时释放算力，从 2026-08-17 起只包括**计费的**那一件 (#442 decision 1)：
# 云 VM 按小时烧钱、而且没有 reaper，所以它必须在这里回收；设备屏是可重建的工作
# 面、有自己的闲置回收器，而话题合并之后还要接着用它，所以不再动。


@pytest.mark.anyio
async def test_delivery_releases_the_cloud_vm_only(monkeypatch):
    """交付释放计费算力，但不拆工作面：设备屏不关。

    合并不再等于话题结束，所以拆掉正在用的屏是纯粹的损失——重建的会丢掉旁边装好
    的一切（procps、测试要用的 git identity）。
    """
    from app.domain.agent import device_provider
    from app.domain.review.services import AcceptService

    screens: list[tuple] = []

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
    assert screens == []  # 设备屏留着


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
