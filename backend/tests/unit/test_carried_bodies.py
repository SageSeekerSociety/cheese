"""这一轮带的正文：发货技能平台手里就有，机器上的那几条现读回来。

`carried_bodies` 是这条路上唯一的判据，所以钉在这里 —— 尤其是那个「不该问机器就
一个字都不问」的性质：轮次组装在热路径上，而提示词逐字节不变靠的正是它。
"""

from __future__ import annotations

import pytest

from app.domain.agent import carried_bodies as module
from app.domain.agent.carried_bodies import carried_bodies


class _Row:
    def __init__(self, *, adopted: bool, digest: str = "d1", root: str = "/home/u/x"):
        self.confirmed_by = "1" if adopted else None
        self.digest = digest
        self.source_root = root


class _Service:
    def __init__(self, rows: dict[str, _Row]):
        self._rows = rows

    def __call__(self, session):  # MachineSkillService(session)
        return self

    async def find(self, device_id: str, name: str) -> _Row | None:
        return self._rows.get(name)


class _Topic:
    def __init__(self, topic_id="t1"):
        self.id = topic_id


@pytest.fixture(autouse=True)
def _clear_cache():
    module._BODIES.clear()
    yield
    module._BODIES.clear()


def _wire(monkeypatch, rows, *, device="dev1", reads=None):
    """把三次外部调用都换成桩：索引、设备名、设备上的 `cat`。"""
    monkeypatch.setattr(module, "MachineSkillService", _Service(rows))

    def _room_device(topic):
        return device

    monkeypatch.setattr(module, "room_device", _room_device)
    calls: list[tuple] = []

    async def _exec(device_id, argv, *, timeout):
        calls.append((device_id, tuple(argv)))
        if reads is None:
            return {"exit": 1, "stderr": "no", "stdout": ""}
        return {"exit": 0, "stdout": reads, "truncated": False}

    monkeypatch.setattr(module.device_hub, "exec", _exec)
    return calls


async def test_all_shipped_names_never_touch_the_machine(monkeypatch):
    calls = _wire(monkeypatch, {})
    topic = _Topic()
    bodies = await carried_bodies(None, topic, ["wolfram"])
    assert bodies  # 平台手里的正文
    assert calls == [], "带的全是发货技能时不该有一次设备往返"


async def test_nothing_carried_is_the_empty_string(monkeypatch):
    calls = _wire(monkeypatch, {})
    assert await carried_bodies(None, _Topic(), []) == ""
    assert calls == []


async def test_an_adopted_machine_skill_is_read_off_the_machine(monkeypatch):
    calls = _wire(monkeypatch, {"ponytail": _Row(adopted=True)}, reads="Do less.\n")
    bodies = await carried_bodies(None, _Topic(), ["ponytail"])
    assert bodies == "Do less."
    assert calls == [("dev1", ("cat", "/home/u/x/SKILL.md"))]


async def test_an_indexed_machine_skill_is_carried_without_adoption(monkeypatch):
    # 索引里有这一行就算带上：机器自己发布的索引已经把话说清了，不再先「采纳」一道
    # （`3c523452` 拿掉了那道门限）。
    calls = _wire(monkeypatch, {"ponytail": _Row(adopted=False)}, reads="Do less.\n")
    assert await carried_bodies(None, _Topic(), ["ponytail"]) == "Do less."
    assert calls == [("dev1", ("cat", "/home/u/x/SKILL.md"))]


async def test_a_machine_that_cannot_answer_just_drops_the_skill(monkeypatch):
    rows = {"ponytail": _Row(adopted=True)}
    monkeypatch.setattr(module, "MachineSkillService", _Service(rows))

    def _room_device(topic):
        return "dev1"

    async def _exec(device_id, argv, *, timeout):
        raise module.DeviceOffline(device_id)

    monkeypatch.setattr(module, "room_device", _room_device)
    monkeypatch.setattr(module.device_hub, "exec", _exec)
    assert await carried_bodies(None, _Topic(), ["ponytail"]) == ""


async def test_a_room_with_no_machine_has_no_machine_skills(monkeypatch):
    calls = _wire(monkeypatch, {"ponytail": _Row(adopted=True)}, device=None, reads="x")
    assert await carried_bodies(None, _Topic(), ["ponytail"]) == ""
    assert calls == []


async def test_the_second_turn_reuses_the_body_it_already_read(monkeypatch):
    calls = _wire(monkeypatch, {"ponytail": _Row(adopted=True)}, reads="Do less.\n")
    topic = _Topic()
    assert await carried_bodies(None, topic, ["ponytail"]) == "Do less."
    assert await carried_bodies(None, topic, ["ponytail"]) == "Do less."
    assert len(calls) == 1


async def test_a_changed_digest_is_read_again(monkeypatch):
    rows = {"ponytail": _Row(adopted=True, digest="d1")}
    calls: list[str] = []
    monkeypatch.setattr(module, "MachineSkillService", _Service(rows))

    def _room_device(topic):
        return "dev1"

    async def _exec(device_id, argv, *, timeout):
        calls.append(device_id)
        return {
            "exit": 0,
            "stdout": f"body-{rows['ponytail'].digest}",
            "truncated": False,
        }

    monkeypatch.setattr(module, "room_device", _room_device)
    monkeypatch.setattr(module.device_hub, "exec", _exec)
    topic = _Topic()
    assert await carried_bodies(None, topic, ["ponytail"]) == "body-d1"
    assert await carried_bodies(None, topic, ["ponytail"]) == "body-d1"
    assert len(calls) == 1
    rows["ponytail"] = _Row(adopted=True, digest="d2")
    assert await carried_bodies(None, topic, ["ponytail"]) == "body-d2"
    assert len(calls) == 2, "digest 变了要重新读一次"
