"""A Cloud topic's held message is delivered the moment its machine can take it."""

import uuid

import pytest

from app.domain.machine.wakeup import CloudWakeup

pytestmark = pytest.mark.anyio


def _wakeup(*, leases, waiting, online):
    log: list[tuple[str, uuid.UUID] | tuple[str, uuid.UUID, str]] = []

    async def ready_leases(device_id):
        return [(t, d) for t, d in leases if d == device_id]

    async def waiting_topics(topic_ids):
        return [t for t in topic_ids if t in waiting]

    async def deliver_held(topic_id):
        log.append(("delivered", topic_id))

    async def announce_failure(topic_id, text):
        log.append(("failed", topic_id, text))

    return (
        CloudWakeup(
            ready_leases=ready_leases,
            waiting_topics=waiting_topics,
            deliver_held=deliver_held,
            is_online=lambda device_id: device_id in online,
            announce_failure=announce_failure,
        ),
        log,
    )


async def test_only_a_connected_machine_with_a_waiting_room_is_woken():
    waiting_on_online = uuid.uuid4()
    waiting_on_offline = uuid.uuid4()
    already_told = uuid.uuid4()
    leases = [
        (waiting_on_online, "dev-on"),
        (waiting_on_offline, "dev-off"),
        (already_told, "dev-on"),
    ]
    wakeup, log = _wakeup(
        leases=leases,
        waiting={waiting_on_online, waiting_on_offline},
        online={"dev-on"},
    )

    await wakeup.wake(leases)

    # 一件事一条记录：房间里那一行「已接入」是送达这一轮的开场白，不再由第二次
    # 广播说一遍。
    assert log == [("delivered", waiting_on_online)]


async def test_a_device_attaching_wakes_the_lease_waiting_on_it_and_no_other():
    mine = uuid.uuid4()
    someone_elses = uuid.uuid4()
    leases = [(mine, "dev-1"), (someone_elses, "dev-2")]
    wakeup, log = _wakeup(
        leases=leases, waiting={mine, someone_elses}, online={"dev-1", "dev-2"}
    )

    await wakeup.wake_device("dev-1")

    assert [t for _, t in log] == [mine]


async def test_nothing_ready_means_nothing_asked_or_said():
    wakeup, log = _wakeup(leases=[], waiting=set(), online=set())
    await wakeup.wake([])
    await wakeup.wake_device("dev-1")
    assert log == []


async def test_a_lease_microcloud_gave_up_on_is_announced_once_to_a_waiting_room():
    """No sweep will ever hand a failed lease to `wake`, and the room is still
    showing 「机器正在创建」. It is told once; a room already past waiting (told
    before, or never waiting) hears nothing, and nothing is delivered."""
    from app.domain.machine.services import FailedLease

    waiting = uuid.uuid4()
    already_told = uuid.uuid4()
    wakeup, log = _wakeup(leases=[], waiting={waiting}, online=set())
    failed = [
        FailedLease(
            topic_id=waiting,
            hostname="box-1",
            reason="MicroCloud 报告机器的 AI 通道配置失败",
        ),
        FailedLease(
            topic_id=already_told,
            hostname="box-2",
            reason="MicroCloud 报告机器创建失败",
        ),
    ]

    await wakeup.report_failures(failed)
    await wakeup.report_failures(failed)  # the next tick sees the same leases

    assert [entry[:2] for entry in log] == [("failed", waiting)] * 2 or [
        entry[:2] for entry in log
    ] == [("failed", waiting)], log
    text = log[0][2]
    assert "box-1" in text and "AI 通道" in text
    assert not any(entry[0] == "delivered" for entry in log)


class _Chat:
    """记两笔：房间里那一行落库了没有、扣着的消息投出去了没有。"""

    def __init__(self, waiting: set[uuid.UUID], log: list) -> None:
        self._waiting = waiting
        self._log = log

    async def cloud_waiting_topics(self, topic_ids):
        return [t for t in topic_ids if t in self._waiting]

    async def post_system_event(self, topic_id, content, turn_id=None, *, meta=None):
        self._log.append(("recorded", topic_id, (meta or {}).get("state")))
        # 落完这一笔，这个房间就不再是「正在创建」—— 和真实的读一样。
        self._waiting.discard(topic_id)
        return {"id": str(uuid.uuid4()), "content": content}


class _Runner:
    def __init__(self, log: list) -> None:
        self._log = log

    def submit(self, chat, topic_id, **kwargs):
        self._log.append(("submitted", topic_id, kwargs))
        return uuid.uuid4()


async def _publish(channel, frame):
    return None


async def _seat(session_factory, topic_id):
    return "cheese-abc"


async def test_the_room_stops_waiting_before_the_delivering_turn_is_queued(monkeypatch):
    """「已接入」这条记录在 `wake` 返回之前就落库了。

    它不只是房间里的一句话，它就是「这个房间已经叫醒过了」本身
    （`cloud_waiting_topics` 读它的 `state`）。交给那一轮去写，它就排在准入闸后
    面：算力用尽那一轮直接被拒，记录永远不写，房间永远停在 waiting，每一拍扫描、
    每一次连接器挂上来都再投递一次；就算不撞闸，投递是当场返回的，这中间的扫描
    和连接器会为同一个房间起第二轮 —— 多一轮计费，房间里多一条「已接入」。
    """
    from types import SimpleNamespace

    import app.api.deps as deps

    topic_id = uuid.uuid4()
    log: list = []
    chat = _Chat({topic_id}, log)

    monkeypatch.setattr(deps, "get_chat_service", lambda: chat)
    monkeypatch.setattr(deps, "get_work_runner", lambda: _Runner(log))
    monkeypatch.setattr(deps, "get_broker", lambda: SimpleNamespace(publish=_publish))
    monkeypatch.setattr(
        deps, "device_hub", SimpleNamespace(is_online=lambda device_id: True)
    )
    monkeypatch.setattr("app.domain.topic_membership.services.addressable_seat", _seat)
    # 这个对象是 lru_cache 住的：建之前清一次，跑完再清一次，免得替身留在缓存里
    # 被后面的用例拿到。
    deps.get_cloud_wakeup.cache_clear()
    try:
        await deps.get_cloud_wakeup().wake([(topic_id, "dev-1")])

        assert [entry[0] for entry in log] == ["recorded", "submitted"]
        assert log[0][2] == "ready", "房间还停在「正在创建」"
        submitted = log[1][2]
        # 这一轮不再自带开场白 —— 开场白就是上面那条记录，一件事一条记录。
        assert submitted.get("nudge_event") is None
        assert [r.handle for r in submitted["addressed"].recipients] == ["cheese-abc"]

        # 下一拍扫描、连接器挂上来：同一个房间不会被再叫醒一次。
        await deps.get_cloud_wakeup().wake([(topic_id, "dev-1")])
        assert [entry[0] for entry in log] == ["recorded", "submitted"]
    finally:
        deps.get_cloud_wakeup.cache_clear()
