"""``SpaceMaterialService.readable_material_ids`` 整批只查两次：关联表一次、管理员表一次。

素材包一次带十几份，从前逐份调 ``may_read_outside_space``，每份一次关联查询、
再逐板一次 ``space_seats.runs``。这里用一个按表记账的假会话钉住次数，同时钉住
判据没变：「所有成员」档对任何人可读，「仅管理员」档只对那块板的管理员可读。

最后一条用例把实现换回逐份调用，证明两条计数断言会红 —— 计数器不是摆设。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.space import material_service
from app.domain.space.material_service import SpaceMaterialService
from app.domain.space.models import (
    SpaceAdminRelation,
    SpaceMaterial,
    SpaceMaterialVisibility,
)
from app.domain.space.repositories import SpaceAdminRelationRepository

MEMBERS = SpaceMaterialVisibility.MEMBERS.value
ADMINS = next(v.value for v in SpaceMaterialVisibility if v.value != MEMBERS)

BOARD_A, BOARD_B = 10, 20
ADMIN_OF_B, OUTSIDER = 7, 8

# 三份素材、两块板：
#   1 —— A 板「所有成员」档：人人可读。
#   2 —— A、B 两板都是「仅管理员」档：只有 A 或 B 的管理员可读。
#   3 —— B 板「仅管理员」档：只有 B 的管理员可读。
LINKS = [
    SimpleNamespace(material_id=1, space_id=BOARD_A, visibility=MEMBERS),
    SimpleNamespace(material_id=2, space_id=BOARD_A, visibility=ADMINS),
    SimpleNamespace(material_id=2, space_id=BOARD_B, visibility=ADMINS),
    SimpleNamespace(material_id=3, space_id=BOARD_B, visibility=ADMINS),
]
ADMINS_BY_BOARD = {BOARD_B: {ADMIN_OF_B}}


def _as_set(value) -> set:
    return set(value) if isinstance(value, list | tuple | set) else {value}


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list:
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _CountingSession:
    """按语句查的那张表记账，并照语句里的参数从上面的夹具里挑行。"""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def execute(self, stmt) -> _Result:
        entity = stmt.column_descriptions[0]["entity"]
        table = entity.__tablename__
        self.calls[table] = self.calls.get(table, 0) + 1
        params = stmt.compile().params
        if entity is SpaceMaterial:
            wanted = _as_set(params["material_id_1"])
            return _Result([link for link in LINKS if link.material_id in wanted])
        assert entity is SpaceAdminRelation
        boards = _as_set(params["space_id_1"])
        user_id = params["user_id_1"]
        managed = [b for b in boards if user_id in ADMINS_BY_BOARD.get(b, set())]
        # 单行查询（``space_seats.runs``）要一个对象；批量查询只要 space_id。
        whole_row = stmt.column_descriptions[0]["type"] is SpaceAdminRelation
        return _Result(
            [SimpleNamespace(space_id=b) for b in managed] if whole_row else managed
        )


def _service(session: _CountingSession) -> SpaceMaterialService:
    service = SpaceMaterialService.__new__(SpaceMaterialService)
    service._session = session  # type: ignore[assignment]
    service._links = material_service.SpaceMaterialRepository(
        session=session  # type: ignore[arg-type]
    )
    service._storage = MagicMock()
    return service


LINK_TABLE = SpaceMaterial.__tablename__
ADMIN_TABLE = SpaceAdminRelation.__tablename__


@pytest.mark.anyio
async def test_a_batch_queries_each_table_once() -> None:
    session = _CountingSession()
    readable = await _service(session).readable_material_ids(
        material_ids=[1, 2, 3], user_id=OUTSIDER
    )
    assert readable == {1}
    assert session.calls.get(LINK_TABLE) == 1
    assert session.calls.get(ADMIN_TABLE) == 1


@pytest.mark.anyio
async def test_admin_only_tier_opens_only_for_that_boards_admin() -> None:
    session = _CountingSession()
    readable = await _service(session).readable_material_ids(
        material_ids=[1, 2, 3], user_id=ADMIN_OF_B
    )
    assert readable == {1, 2, 3}
    assert session.calls == {LINK_TABLE: 1, ADMIN_TABLE: 1}


@pytest.mark.anyio
async def test_members_tier_needs_no_admin_lookup() -> None:
    session = _CountingSession()
    readable = await _service(session).readable_material_ids(
        material_ids=[1, 99], user_id=OUTSIDER
    )
    # 99 没挂在任何板上：普通素材，口径照旧人人可读。
    assert readable == {1, 99}
    assert session.calls == {LINK_TABLE: 1}


@pytest.mark.anyio
async def test_single_material_gate_keeps_its_answer() -> None:
    service = _service(_CountingSession())
    assert await service.may_read_outside_space(material_id=1, user_id=OUTSIDER)
    assert not await service.may_read_outside_space(material_id=3, user_id=OUTSIDER)
    assert await service.may_read_outside_space(material_id=3, user_id=ADMIN_OF_B)


async def _per_material(self, *, material_ids, user_id) -> set[int]:
    """故意违规：逐份走老路 —— 每份一次关联查询，再逐板查一次管理员关系。"""
    readable: set[int] = set()
    for material_id in material_ids:
        links = await self._links.list_live_by_material_ids(material_ids=[material_id])
        if material_service._readable_by_a_member([x.visibility for x in links]):
            readable.add(material_id)
            continue
        for link in links:
            relation = await SpaceAdminRelationRepository(self._session).get_relation(
                link.space_id, user_id
            )
            if relation is not None:
                readable.add(material_id)
                break
    return readable


@pytest.mark.anyio
async def test_counting_bites_when_split_back_per_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SpaceMaterialService, "readable_material_ids", _per_material)
    session = _CountingSession()
    readable = await _service(session).readable_material_ids(
        material_ids=[1, 2, 3], user_id=OUTSIDER
    )
    # 答案一样，次数不一样 —— 上面两条「各一次」的断言在这条实现下都会红。
    assert readable == {1}
    assert session.calls[LINK_TABLE] != 1
    assert session.calls[ADMIN_TABLE] != 1
