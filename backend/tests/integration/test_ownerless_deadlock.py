"""无主话题 + 无主项目 = 产品里出不去的死角。

`TopicMemberService._require_manager` 早就留了逃生口：房间没有任何 manager 时，
项目的 owner 或 lead 可以补一个进去。逃生口本身是对的——但它假设了「项目总有人
管」，而这个假设不成立：`ProjectService.create` 允许 `owner_handle=None` 且不建
任何成员行，于是 `projects.owner_handle` 为 NULL、一个 lead 也没有的项目是造得
出来的。

那样的项目里，一个丢了 owner 的话题就**两个方向都没有出口**：
只有话题 manager 能指派 owner，而没有；只有项目 steward 能代劳，而也没有；
想给项目补 owner_handle，那道闸门（`require_project_steward`）**同样**要 steward。

dev 上量到的（2026-08-13）：`cheese 自建` 18 个活跃话题里 5 个无 owner，而项目的
`owner_handle` 正是 NULL——它之所以还有救，纯粹因为碰巧有一个 lead。

下面钉的就是：没人管的时候成员可以顶上，有人管的时候一个字都不许多给。
"""

import uuid

from app.domain.project.models import ProjectRole
from tests.integration.conftest import session_auth_headers


def _ownerless_project(client) -> tuple[str, str]:
    """A project with a NULL owner_handle and no members at all."""
    p = client.post("/api/projects", json={"name": "没人管的项目"}).json()["data"]
    return p["id"], p["root_topic_id"]


def _strip_topic_managers(client, topic_id: str) -> None:
    """Leave the room with nobody who can manage it (the state under test)."""
    import asyncio

    from sqlalchemy import delete

    from app.domain.topic.models import TopicMembership, TopicRole

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                delete(TopicMembership).where(
                    TopicMembership.topic_id == uuid.UUID(topic_id),
                    TopicMembership.role.in_([TopicRole.owner, TopicRole.admin]),
                )
            )
            await session.commit()

    asyncio.run(_go())


def _add_project_member(client, project_id: str, handle: str, role) -> None:
    """Seed the roster directly.

    Not through `POST /projects/{id}/members`: that endpoint is itself gated on
    `can_manage_project_members`, so on an ownerless project it 403s — which is
    the same dead end from the other side, and not what these tests are about.
    """
    import asyncio

    from app.domain.project.models import ProjectMember

    async def _go() -> None:
        async with client.test_factory() as session:
            session.add(
                ProjectMember(
                    project_id=uuid.UUID(project_id), user_handle=handle, role=role
                )
            )
            await session.commit()

    asyncio.run(_go())


def _claim_owner(client, topic_id: str, who: str):
    return client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": who, "role": "owner", "actor": who},
        headers=session_auth_headers(who),
    )


def test_a_member_can_step_in_when_nobody_is_in_charge(client):
    """The dead end. Project owner NULL, no lead, room has no manager — before
    this, the only repair was writing the database by hand."""
    pid, tid = _ownerless_project(client)
    _add_project_member(client, pid, "alice", ProjectRole.member)
    _strip_topic_managers(client, tid)

    assert _claim_owner(client, tid, "alice").status_code == 200


def test_a_project_with_a_lead_does_not_dilute_to_its_members(client):
    """The widening must be the narrowest one that removes the dead end. While
    somebody CAN answer for the project, a plain member still cannot."""
    pid, tid = _ownerless_project(client)
    _add_project_member(client, pid, "carol", ProjectRole.lead)
    _add_project_member(client, pid, "alice", ProjectRole.member)
    _strip_topic_managers(client, tid)

    assert _claim_owner(client, tid, "alice").status_code == 403
    assert _claim_owner(client, tid, "carol").status_code == 200


def test_a_project_with_an_owner_does_not_dilute_either(client):
    """Same rule via the other half of 'in charge': owner_handle is set."""
    p = client.post(
        "/api/projects", json={"name": "有主", "owner_handle": "dave"}
    ).json()["data"]
    _add_project_member(client, p["id"], "alice", ProjectRole.member)
    _strip_topic_managers(client, p["root_topic_id"])

    assert _claim_owner(client, p["root_topic_id"], "alice").status_code == 403
    assert _claim_owner(client, p["root_topic_id"], "dave").status_code == 200


def test_an_outsider_never_steps_in(client):
    """Nobody in charge is not the same as everybody in charge. The hatch opens
    for the project's own members, and for no one else."""
    pid, tid = _ownerless_project(client)
    _add_project_member(client, pid, "alice", ProjectRole.member)
    _strip_topic_managers(client, tid)

    assert _claim_owner(client, tid, "mallory").status_code == 403


def test_a_healthy_room_keeps_its_owner_safe(client):
    """The pre-existing guarantee, unchanged: the hatch opens only while the
    ROOM has no manager. A room with an owner is never overridden."""
    pid, tid = _ownerless_project(client)
    _add_project_member(client, pid, "alice", ProjectRole.member)
    _strip_topic_managers(client, tid)
    assert _claim_owner(client, tid, "alice").status_code == 200

    # alice now owns the room; a second member must not be able to take it.
    _add_project_member(client, pid, "bob", ProjectRole.member)
    assert _claim_owner(client, tid, "bob").status_code == 403
