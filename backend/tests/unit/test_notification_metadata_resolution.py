from datetime import UTC, datetime

import pytest

from app.domain.notification.dto import ResolvedEntityInfoDTO
from app.domain.notification.entity_resolvers import EntityInfoResolver
from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.services import NotificationQueryService


class _StubResolver(EntityInfoResolver):
    def __init__(
        self, entity_type: str, mapping: dict[str, ResolvedEntityInfoDTO | None]
    ) -> None:
        self._entity_type = entity_type
        self._mapping = mapping

    def supported_entity_type(self) -> str:
        return self._entity_type

    async def resolve(self, entity_ids):  # type: ignore[override]
        return {entity_id: self._mapping.get(entity_id) for entity_id in entity_ids}


@pytest.mark.anyio
async def test_recursive_metadata_resolution_handles_nested_arrays() -> None:
    service = NotificationQueryService(
        repo=object(),
        resolvers=[
            _StubResolver(
                "user",
                {
                    "1": ResolvedEntityInfoDTO(id="1", type="user", name="Ada"),
                    "2": ResolvedEntityInfoDTO(id="2", type="user", name="Bob"),
                },
            ),
            _StubResolver(
                "team",
                {
                    "8": ResolvedEntityInfoDTO(id="8", type="team", name="Ops"),
                },
            ),
        ],
    )

    metadata = {
        "actor": {"type": "user", "id": "1"},
        "payload": {
            "mentions": [
                {"type": "user", "id": "2"},
            ],
            "team": {"type": "team", "id": "8"},
        },
    }

    resolved = await service.resolve_entities_from_metadata([metadata])

    assert resolved["actor"].name == "Ada"
    assert resolved["payload.mentions[0]"].name == "Bob"
    assert resolved["payload.team"].type == "team"

    notification = Notification(
        id=123,
        receiver_id=1,
        type=NotificationType.MENTION,
        metadata_payload=metadata,
        read=False,
        is_aggregatable=False,
        aggregation_key=None,
        aggregate_until=None,
        finalized=True,
        version=0,
        created_at=datetime.now(UTC),
        updated_at=None,
        deleted_at=None,
    )

    dto = await service.build_notification_dto(notification)
    assert dto.entities["actor"].name == "Ada"
    assert dto.entities["payload.mentions[0]"].name == "Bob"
    assert "actor" not in dto.contextMetadata
    assert dto.contextMetadata["payload"]["mentions"] == []


class _RecordingResolver(EntityInfoResolver):
    """记下每次被问到的那一批 id —— 整页解析的要点就是问几次、一次问多少。"""

    def __init__(
        self, entity_type: str, mapping: dict[str, ResolvedEntityInfoDTO | None]
    ) -> None:
        self._entity_type = entity_type
        self._mapping = mapping
        self.calls: list[list[str]] = []

    def supported_entity_type(self) -> str:
        return self._entity_type

    async def resolve(self, entity_ids):  # type: ignore[override]
        self.calls.append(list(entity_ids))
        return {entity_id: self._mapping.get(entity_id) for entity_id in entity_ids}


def _notification(row_id: int, metadata: dict) -> Notification:
    return Notification(
        id=row_id,
        receiver_id=1,
        type=NotificationType.MENTION,
        metadata_payload=metadata,
        read=False,
        is_aggregatable=False,
        aggregation_key=None,
        aggregate_until=None,
        finalized=True,
        version=0,
        created_at=datetime.now(UTC),
        updated_at=None,
        deleted_at=None,
    )


@pytest.mark.anyio
async def test_a_page_asks_each_entity_type_once() -> None:
    """一页 N 条，每类实体只问一次，问的是整页攒起来的那批 id。"""
    users = _RecordingResolver(
        "user",
        {
            "1": ResolvedEntityInfoDTO(id="1", type="user", name="Ada"),
            "2": ResolvedEntityInfoDTO(id="2", type="user", name="Bob"),
            "3": ResolvedEntityInfoDTO(id="3", type="user", name="Cyd"),
        },
    )
    service = NotificationQueryService(repo=object(), resolvers=[users])

    dtos = await service.build_notification_dtos(
        [
            _notification(1, {"actor": {"type": "user", "id": "1"}}),
            _notification(
                2,
                {
                    "actor": {"type": "user", "id": "2"},
                    "target": {"type": "user", "id": "3"},
                },
            ),
        ]
    )

    assert users.calls == [["1", "2", "3"]]
    assert [dto.entities["actor"].name for dto in dtos] == ["Ada", "Bob"]
    assert dtos[1].entities["target"].name == "Cyd"


@pytest.mark.anyio
async def test_a_page_keeps_each_notifications_entities_to_itself() -> None:
    """两条通知各有自己的顶层 ``actor``，批量解析不能把它们并成一张表。

    `resolve_entities_from_metadata` 返回的是按 path 拍平的字典：整页一起摊就是一
    个 ``actor`` 键，第二条会盖掉第一条。摊回必须是**一条一份**。
    """
    service = NotificationQueryService(
        repo=object(),
        resolvers=[
            _StubResolver(
                "user",
                {
                    "1": ResolvedEntityInfoDTO(id="1", type="user", name="Ada"),
                    "2": ResolvedEntityInfoDTO(id="2", type="user", name="Bob"),
                },
            )
        ],
    )

    dtos = await service.build_notification_dtos(
        [
            _notification(1, {"actor": {"type": "user", "id": "1"}}),
            _notification(2, {"actor": {"type": "user", "id": "2"}}),
            _notification(3, {}),
        ]
    )

    assert dtos[0].entities["actor"].name == "Ada"
    assert dtos[1].entities["actor"].name == "Bob"
    assert dtos[2].entities == {}
