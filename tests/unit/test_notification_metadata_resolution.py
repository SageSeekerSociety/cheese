from datetime import datetime, timezone

import pytest

from app.domain.notification.dto import ResolvedEntityInfoDTO
from app.domain.notification.entity_resolvers import EntityInfoResolver
from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.services import NotificationQueryService


class _StubResolver(EntityInfoResolver):
    def __init__(self, entity_type: str, mapping: dict[str, ResolvedEntityInfoDTO | None]) -> None:
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
        created_at=datetime.now(timezone.utc),
        updated_at=None,
        deleted_at=None,
    )

    dto = await service.build_notification_dto(notification)
    assert dto.entities["actor"].name == "Ada"
    assert dto.entities["payload.mentions[0]"].name == "Bob"
    assert "actor" not in dto.contextMetadata
    assert dto.contextMetadata["payload"]["mentions"] == []
