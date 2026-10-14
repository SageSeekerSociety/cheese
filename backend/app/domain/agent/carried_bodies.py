"""这一轮带的技能正文：平台发货的照旧，机器上的按 ``source_root`` 读回来。

``native_skill_bodies`` 只认得平台随会话发货的那几个名字，别的名字它一声不响地
丢掉（``native_skill_doc`` 返回 ``None``）。技能正文搬到机器上（``machine_skill``）
以后，一个队友带的名字可能是一条索引行 —— 正文在那台机器上，只有一份，平台只能
现读，读不到就当这条没带。

**这是一次设备往返，所以它只在真有机器技能要读的时候发生**：带的全是发货技能，
或者这台房号根本没配机器，就一个字节都不去问。两个名单都空时返回空串，
``turn.py`` 那条「提示词逐字节不变」的性质因此照旧成立。
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.device_hub import DeviceOffline, device_hub
from app.domain.machine_skill.service import MachineSkillService
from app.domain.skill_library import native_skill_bodies, shipped_skill_names

#: Platform skills, for telling「这个名字平台手里有正文」from「得去机器上读」.
_SHIPPED = frozenset(shipped_skill_names())

#: How long one turn waits on a machine for one skill's text. Short on purpose:
#: a laptop somebody closed must not hold up the whole turn, and a skill that
#: arrived too late is simply not carried this time.
READ_TIMEOUT_S = 10

#: 已经读回来的正文，按 ``(device_id, name, digest)`` 记住 —— digest 变了就是
#: 机器改了它，键自然换一条，读到的永远是当前那一版。
#:
#: ``ponytail:`` 上限：进程内、无 TTL、满了整条清空。一台机器上的技能正文远小于
#: 256 条；升级路径是给它加 TTL，或把正文落到 `machine_skill` 的一张附属表里。
_BODIES: dict[tuple[str, str, str], str] = {}
_BODIES_LIMIT = 256


async def carried_bodies(session: AsyncSession, topic, names: list[str]) -> str:
    """``names`` 的说明书，按 ``native_skill_bodies`` 那套 ``---`` 拼起来。"""
    chunks: list[str] = []
    shipped = native_skill_bodies(names)
    if shipped:
        chunks.append(shipped)
    mine = [name for name in names if name not in _SHIPPED]
    if not mine:
        return "\n\n---\n\n".join(chunks)
    device_id = room_device(topic)
    if device_id is None:
        return "\n\n---\n\n".join(chunks)
    for name in mine:
        body = await _from_machine(session, device_id, name)
        if body:
            chunks.append(body)
    return "\n\n---\n\n".join(chunks)


def room_device(topic) -> str | None:
    """这一轮会落到哪台机器：这间房的屏开在哪台，就是哪台。

    机器的归属在这一层没有一张表可读（`machine` 域持着 lease，agent 域碰不到
    —— 见 `.claude/scripts/check-repo-rules.sh` 的 supply 一条）；agent 域自己
    的 ``device_hub`` 知道每间房的屏挂在谁身上，那就是答案。屏含离线的那些：
    机器刚掉线时这条技能本来就读不回来，``_from_machine`` 自己会把那条丢掉。

    没有屏（云，或者这间房还没碰过机器）就是 ``None`` —— 不认机器，也就没有
    机器技能可读。
    """
    screens = device_hub.screens_for_topic(topic.id)
    return screens[0].device_id if screens else None


async def _from_machine(session: AsyncSession, device_id: str, name: str) -> str:
    """一条机器技能的正文，或者空串（没这条、读不回来）。"""
    row = await MachineSkillService(session).find(device_id, name)
    # 索引里有这一行就读：人把它勾给队友（或装进当前这位）就算带上，不再先「采纳」一道。
    if row is None:
        return ""
    key = (device_id, name, row.digest)
    if key in _BODIES:
        return _BODIES[key]
    body = await _read(device_id, row.source_root)
    if body:
        if len(_BODIES) >= _BODIES_LIMIT:
            _BODIES.clear()
        _BODIES[key] = body
    return body


async def _read(device_id: str, source_root: str) -> str:
    """``cat`` 一份 SKILL.md。读不回来（机器不在、文件没了、太长被截断）就是没有。

    截断也当没读到：半份说明书比没有更糟 —— 它会照着做一半。
    """
    try:
        result = await device_hub.exec(
            device_id,
            ["cat", os.path.join(source_root, "SKILL.md")],
            timeout=READ_TIMEOUT_S,
        )
    except (DeviceOffline, TimeoutError):
        return ""
    if result.get("exit") != 0 or result.get("truncated"):
        return ""
    return str(result.get("stdout") or "").strip()
