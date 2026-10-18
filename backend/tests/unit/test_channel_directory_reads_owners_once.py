"""「全部频道」问频道主人只发一次名册查询，不随频道数增长。

一批十几个频道的目录请求：名册读一次、兜底那半的频道行读一次，每个频道的
``manager`` 与逐频道问一遍相同，其余字段一个不少。判据的每一档都有用例：显式
owner 席位、多个 owner 席位取最早那条、``created_by`` 兜底、``is_private`` 的私聊
一律没有主人、``members_only`` 看创建者还在不在名册里。最后把实现换回逐频道查，
证明「各一次」那条计数断言会红。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.topic import directory
from app.domain.topic.models import TopicKind, TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership import services
from app.domain.topic_membership.repositories import TopicMembershipRepository

MEMBERS_TABLE = "topic_memberships"
TOPICS_TABLE = "topics"

PROJECT = uuid.uuid4()
ROOT = uuid.uuid4()
ALICE_ONE, ALICE_TWO, ALICE_THREE, ALICE_FOUR, ALICE_FIVE = (
    uuid.uuid4() for _ in range(5)
)
HANDED_OVER = uuid.uuid4()
FALLBACK_PUBLIC = uuid.uuid4()
PRIVATE_SEATED = uuid.uuid4()
PRIVATE_GONE = uuid.uuid4()
MEMBERS_ONLY_SEATED = uuid.uuid4()
MEMBERS_ONLY_GONE = uuid.uuid4()
TWO_OWNERS = uuid.uuid4()
ALL_IDS = [
    ROOT,
    ALICE_ONE,
    ALICE_TWO,
    ALICE_THREE,
    ALICE_FOUR,
    ALICE_FIVE,
    HANDED_OVER,
    FALLBACK_PUBLIC,
    PRIVATE_SEATED,
    PRIVATE_GONE,
    MEMBERS_ONLY_SEATED,
    MEMBERS_ONLY_GONE,
    TWO_OWNERS,
]
EXPECTED_OWNERS = {
    ROOT: None,
    ALICE_ONE: "alice",
    ALICE_TWO: "alice",
    ALICE_THREE: "alice",
    ALICE_FOUR: "alice",
    ALICE_FIVE: "alice",
    HANDED_OVER: "bob",
    FALLBACK_PUBLIC: "alice",
    # 私聊（`is_private`）在 `_public` 那一档就被挡掉，创建者在不在里面都没有主人。
    PRIVATE_SEATED: None,
    PRIVATE_GONE: None,
    # 「不公开给人随便进」（`members_only`）的频道不是私聊，照常靠 `created_by`
    # 兜底，但创建者的人还在名册里才回落到他。
    MEMBERS_ONLY_SEATED: "alice",
    MEMBERS_ONLY_GONE: None,
    # 两个 owner 席位时取 `created_at` 最早的那条。
    TWO_OWNERS: "bob",
}


def _as_set(value) -> set:
    return set(value) if isinstance(value, list | tuple | set) else {value}


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _CountingSession:
    """按语句查的那张表记账，并照语句里的 topic id 从夹具里挑行。"""

    def __init__(self, memberships: list, topics: list) -> None:
        self.memberships = memberships
        self.topics = topics
        self.calls: dict[str, int] = {}

    def _count(self, table: str) -> None:
        self.calls[table] = self.calls.get(table, 0) + 1

    async def scalars(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        table = entity.__tablename__
        self._count(table)
        params = stmt.compile().params
        if table == MEMBERS_TABLE:
            wanted = _as_set(params["topic_id_1"])
            rows = [m for m in self.memberships if m.topic_id in wanted]
            # 语句真的带 `ORDER BY created_at ASC` 时才照它排：有人把这个排序
            # 从 `list_for_topics` 拿掉，行序变回插入序，「两个 owner 席位取
            # 最早那条」那条断言就会红。
            if "ORDER BY topic_memberships.created_at ASC" in str(stmt):
                rows = sorted(rows, key=lambda m: m.created_at)
            return _Result(rows)
        assert table == TOPICS_TABLE
        wanted = _as_set(params["id_1"])
        return _Result([t for t in self.topics if t.id in wanted])

    async def get(self, entity, ident):
        self._count(entity.__tablename__)
        return next((t for t in self.topics if t.id == ident), None)


def _membership(topic_id, handle, role, created_at=0):
    return SimpleNamespace(
        topic_id=topic_id,
        member_handle=handle,
        role=role,
        created_at=created_at,
    )


def _topic(topic_id, *, kind=TopicKind.topic, is_private=False, members_only=False):
    return SimpleNamespace(
        id=topic_id,
        project_id=PROJECT,
        title=f"频道 {str(topic_id)[:4]}",
        kind=kind,
        is_private=is_private,
        members_only=members_only,
        created_by="alice",
        description=None,
        status="open",
        created_at=None,
    )


def _fixtures():
    topics = [_topic(ROOT, kind=TopicKind.root)]
    memberships = []
    for topic_id in (ALICE_ONE, ALICE_TWO, ALICE_THREE, ALICE_FOUR, ALICE_FIVE):
        topics.append(_topic(topic_id))
        memberships.append(_membership(topic_id, "alice", TopicRole.owner))
    topics.append(_topic(HANDED_OVER))
    memberships.append(_membership(HANDED_OVER, "bob", TopicRole.owner))
    # 没有 owner 席位、靠 created_by 兜底的公开频道。
    topics.append(_topic(FALLBACK_PUBLIC))
    memberships.append(_membership(FALLBACK_PUBLIC, "alice", TopicRole.member))
    # 私聊，创建者还在里面：`is_private` 一档就挡掉了，没有主人。
    topics.append(_topic(PRIVATE_SEATED, is_private=True))
    memberships.append(_membership(PRIVATE_SEATED, "alice", TopicRole.member))
    # 私聊，创建者已经不在里面：同样没有主人。
    topics.append(_topic(PRIVATE_GONE, is_private=True))
    memberships.append(_membership(PRIVATE_GONE, "bob", TopicRole.member))
    # 不公开给人随便进的频道（不是私聊），创建者还在名册里做 member：回落。
    topics.append(_topic(MEMBERS_ONLY_SEATED, members_only=True))
    memberships.append(_membership(MEMBERS_ONLY_SEATED, "alice", TopicRole.member))
    # 同一档但创建者已经不在名册里：不回落。
    topics.append(_topic(MEMBERS_ONLY_GONE, members_only=True))
    memberships.append(_membership(MEMBERS_ONLY_GONE, "bob", TopicRole.member))
    # 两个 owner 席位（历史数据可能这样）：取 `created_at` 最早的那一条。插入序
    # 故意与时间序相反，取错一头这条就会红。
    topics.append(_topic(TWO_OWNERS))
    memberships.append(_membership(TWO_OWNERS, "alice", TopicRole.owner, created_at=2))
    memberships.append(_membership(TWO_OWNERS, "bob", TopicRole.owner, created_at=1))
    return memberships, topics


def _service(session: _CountingSession) -> services.TopicMemberService:
    svc = services.TopicMemberService.__new__(services.TopicMemberService)
    svc._repo = TopicMembershipRepository(session)  # type: ignore[arg-type]
    svc._topics = TopicRepository(session)  # type: ignore[arg-type]
    return svc


def _read_the_foil_once(session: _CountingSession) -> None:
    """名册一次、频道行一次 —— 逐频道查的实现在这里红。"""
    assert session.calls.get(MEMBERS_TABLE) == 1
    assert session.calls.get(TOPICS_TABLE) == 1


@pytest.mark.anyio
async def test_every_channel_asks_who_owns_it_once() -> None:
    memberships, topics = _fixtures()
    session = _CountingSession(memberships, topics)
    owners = await _service(session).owners_of(ALL_IDS)
    assert owners == EXPECTED_OWNERS
    _read_the_foil_once(session)


@pytest.mark.anyio
async def test_one_channel_answers_the_same_as_the_batch() -> None:
    memberships, topics = _fixtures()
    for topic_id, expected in EXPECTED_OWNERS.items():
        svc = _service(_CountingSession(memberships, topics))
        assert await svc.owner_of(topic_id) == expected


@pytest.mark.anyio
async def test_single_channel_goes_through_the_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`owner_of` 走门面转发：打在 `owners_of` 上的补丁拦得住这一次调用。

    包里「跨块调用一律走 `svc.<名字>()`」那条约定靠的就是这个 —— 直接调模块函数
    的话，别人 `patch.object(svc, "owners_of", ...)` 拦不住。
    """
    seen: list[list[uuid.UUID]] = []

    async def patched(self, topic_ids):
        seen.append(list(topic_ids))
        return {topic_id: "patched" for topic_id in topic_ids}

    monkeypatch.setattr(services.TopicMemberService, "owners_of", patched)
    memberships, topics = _fixtures()
    svc = _service(_CountingSession(memberships, topics))
    assert await svc.owner_of(ALICE_ONE) == "patched"
    assert seen == [[ALICE_ONE]]
    # 补丁拦住了，名册一次都没读。
    assert svc._repo._session.calls == {}


def _per_channel(self, topic_ids):
    """故意违规：换回逐频道查 —— 每个频道一次名册，缺主人时再一次频道行。"""

    async def answer(topic_id):
        members = await self._repo.list_for_topic(topic_id)
        member = next((m for m in members if m.role == TopicRole.owner), None)
        if member is not None:
            return member.member_handle
        topic = await self._topics.get(topic_id)
        if (
            topic is not None
            and not topic.is_private
            and topic.kind != TopicKind.root
            and topic.created_by
            and not self._is_agent_handle(topic.created_by)
            and (
                not topic.members_only
                or any(m.member_handle == topic.created_by for m in members)
            )
        ):
            return topic.created_by
        return None

    async def owners_of(topic_ids):
        return {topic_id: await answer(topic_id) for topic_id in topic_ids}

    return owners_of(topic_ids)


@pytest.mark.anyio
async def test_going_back_to_one_read_per_channel_makes_the_count_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services.TopicMemberService, "owners_of", _per_channel)
    memberships, topics = _fixtures()
    session = _CountingSession(memberships, topics)
    owners = await _service(session).owners_of(ALL_IDS)
    # 答案一样，次数不一样：上面那条「各一次」的断言在这条实现下会红。
    assert owners == EXPECTED_OWNERS
    assert session.calls[MEMBERS_TABLE] == len(ALL_IDS)
    with pytest.raises(AssertionError):
        _read_the_foil_once(session)


class _Members:
    """递进 ``channel_directory`` 的 `TopicMembership`：主人只许整批问。"""

    def __init__(self, topics: list, owners: dict) -> None:
        self.topics = topics
        self.owners = owners
        self.batch_calls = 0
        self.single_calls = 0

    async def seen(self, topics, handle, *, team):
        return list(topics)

    async def joined_topic_ids(self, topics, handle, *, team):
        return set()

    async def seat_counts(self, topics, *, team):
        return {}

    async def managed_topic_ids(self, topics, actor):
        return set()

    async def owners_of(self, topic_ids):
        self.batch_calls += 1
        assert set(topic_ids) == {t.id for t in self.topics}
        return dict(self.owners)

    async def owner_of(self, topic_id):
        self.single_calls += 1
        raise AssertionError("目录又逐频道问主人了")


class _DirectorySession:
    """列表那两条批量读：频道行与最后活跃。"""

    def __init__(self, topics: list) -> None:
        self.topics = topics
        self.calls: dict[str, int] = {}

    async def scalars(self, stmt):
        table = stmt.column_descriptions[0]["entity"].__tablename__
        self.calls[table] = self.calls.get(table, 0) + 1
        return _Result(self.topics)

    async def execute(self, stmt):
        return _Result([(t.id, None) for t in self.topics])


@pytest.mark.anyio
async def test_the_directory_asks_for_every_manager_in_one_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, topics = _fixtures()
    session = _DirectorySession(topics)
    members = _Members(topics, EXPECTED_OWNERS)
    monkeypatch.setattr(directory, "may", AsyncMock(return_value=True))

    answer = await directory.channel_directory(
        session,
        PROJECT,
        "dave",
        team=SimpleNamespace(),
        counts=SimpleNamespace(underway_counts=AsyncMock(return_value={})),
        members=members,
    )

    assert members.batch_calls == 1
    assert members.single_calls == 0
    assert answer["manages_project"] is True
    items = answer["items"]
    assert {item["id"]: item["manager"] for item in items} == {
        str(topic_id): manager for topic_id, manager in EXPECTED_OWNERS.items()
    }
    # 其余字段与改前逐字段一致。
    expected = []
    for topic in topics:
        general = topic.kind == TopicKind.root
        expected.append(
            {
                "id": str(topic.id),
                "title": topic.title,
                "general": general,
                "members_only": topic.members_only,
                "archived": False,
                "joined": False,
                "visible": True,
                "description": topic.description,
                "member_count": 0,
                "open_tasks": 0,
                "last_activity_at": None,
                "manager": EXPECTED_OWNERS[topic.id],
                "can_manage": False,
                "can_administer": not general,
            }
        )
    assert items == expected
