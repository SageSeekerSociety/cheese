from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.notification.dto import ResolvedEntityInfoDTO
from app.domain.notification.models import NotificationType
from app.domain.notification.services import NotificationQueryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _notification(
    *,
    id: int = 1,
    receiver_id: int = 10,
    type: NotificationType = NotificationType.MENTION,
    read: bool = False,
    metadata_payload: dict | None = None,
    created_at: datetime = NOW,
    **kw,
):
    defaults = {
        "id": id,
        "receiver_id": receiver_id,
        "type": type,
        "read": read,
        "metadata_payload": metadata_payload,
        "created_at": created_at,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class _FakeResolver:
    """Fake EntityInfoResolver for testing."""

    def __init__(self, entity_type: str, results: dict[str, ResolvedEntityInfoDTO | None]):
        self._type = entity_type
        self._results = results

    def supported_entity_type(self) -> str:
        return self._type

    async def resolve(self, entity_ids):
        return {eid: self._results.get(eid) for eid in entity_ids}


def _make_service(repo=None, resolvers=None):
    return NotificationQueryService(
        repo=repo or AsyncMock(),
        resolvers=resolvers,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_no_resolvers():
    svc = _make_service()
    assert svc._resolver_map == {}


def test_init_with_resolvers():
    resolver = _FakeResolver("team", {})
    svc = _make_service(resolvers=[resolver])
    assert "team" in svc._resolver_map
    assert svc._resolver_map["team"] is resolver


# ---------------------------------------------------------------------------
# get_notification_by_id_for_current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_notification_by_id_found():
    repo = AsyncMock()
    notif = _notification(id=5)
    repo.get_by_id_for_user.return_value = notif

    svc = _make_service(repo=repo)
    result = await svc.get_notification_by_id_for_current_user(user_id=10, notification_id=5)

    repo.get_by_id_for_user.assert_awaited_once_with(user_id=10, notification_id=5)
    assert result is notif


@pytest.mark.anyio
async def test_get_notification_by_id_not_found():
    repo = AsyncMock()
    repo.get_by_id_for_user.return_value = None

    svc = _make_service(repo=repo)
    result = await svc.get_notification_by_id_for_current_user(user_id=10, notification_id=999)

    assert result is None


# ---------------------------------------------------------------------------
# get_notifications_for_current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_notifications_basic():
    repo = AsyncMock()
    n1 = _notification(id=1)
    n2 = _notification(id=2)
    repo.list_for_user.return_value = [n1, n2]

    svc = _make_service(repo=repo)
    result = await svc.get_notifications_for_current_user(user_id=10, limit=20)

    repo.list_for_user.assert_awaited_once_with(
        user_id=10,
        limit=20,
        cursor_created_at=None,
        cursor_id=None,
        type_=None,
        read=None,
    )
    assert len(result) == 2


@pytest.mark.anyio
async def test_get_notifications_with_all_filters():
    repo = AsyncMock()
    repo.list_for_user.return_value = []

    svc = _make_service(repo=repo)
    cursor_time = datetime(2026, 1, 10, tzinfo=UTC)
    result = await svc.get_notifications_for_current_user(
        user_id=10,
        limit=5,
        cursor_created_at=cursor_time,
        cursor_id=100,
        type_=NotificationType.REPLY,
        read=True,
    )

    repo.list_for_user.assert_awaited_once_with(
        user_id=10,
        limit=5,
        cursor_created_at=cursor_time,
        cursor_id=100,
        type_=NotificationType.REPLY,
        read=True,
    )
    assert result == []


# ---------------------------------------------------------------------------
# mark_all_as_read_for_current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mark_all_as_read():
    repo = AsyncMock()
    repo.mark_all_as_read_for_user.return_value = 3

    svc = _make_service(repo=repo)
    result = await svc.mark_all_as_read_for_current_user(user_id=10)

    repo.mark_all_as_read_for_user.assert_awaited_once_with(user_id=10)
    assert result == 3


# ---------------------------------------------------------------------------
# set_read_status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_read_status():
    repo = AsyncMock()
    repo.set_read_status_for_user.return_value = 1

    svc = _make_service(repo=repo)
    result = await svc.set_read_status(user_id=10, notification_id=5, desired_read_status=True)

    repo.set_read_status_for_user.assert_awaited_once_with(
        user_id=10, notification_id=5, read=True
    )
    assert result == 1


@pytest.mark.anyio
async def test_set_read_status_no_match():
    repo = AsyncMock()
    repo.set_read_status_for_user.return_value = 0

    svc = _make_service(repo=repo)
    result = await svc.set_read_status(user_id=10, notification_id=999, desired_read_status=False)

    assert result == 0


# ---------------------------------------------------------------------------
# get_unread_notification_count_for_current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_unread_count():
    repo = AsyncMock()
    repo.count_unread_for_user.return_value = 7

    svc = _make_service(repo=repo)
    result = await svc.get_unread_notification_count_for_current_user(user_id=10)

    repo.count_unread_for_user.assert_awaited_once_with(user_id=10)
    assert result == 7


# ---------------------------------------------------------------------------
# count_notifications_for_current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_count_notifications_no_filters():
    repo = AsyncMock()
    repo.count_for_user.return_value = 42

    svc = _make_service(repo=repo)
    result = await svc.count_notifications_for_current_user(user_id=10)

    repo.count_for_user.assert_awaited_once_with(user_id=10, type_=None, read=None)
    assert result == 42


@pytest.mark.anyio
async def test_count_notifications_with_filters():
    repo = AsyncMock()
    repo.count_for_user.return_value = 5

    svc = _make_service(repo=repo)
    result = await svc.count_notifications_for_current_user(
        user_id=10, type_=NotificationType.REACTION, read=False
    )

    repo.count_for_user.assert_awaited_once_with(
        user_id=10, type_=NotificationType.REACTION, read=False
    )
    assert result == 5


# ---------------------------------------------------------------------------
# bulk_set_read_status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bulk_set_read_status_empty_updates():
    svc = _make_service()
    result = await svc.bulk_set_read_status(user_id=10, updates=[])

    assert result == []


@pytest.mark.anyio
async def test_bulk_set_read_status_updates_matching():
    repo = AsyncMock()
    n1 = _notification(id=1, read=False)
    n2 = _notification(id=2, read=True)
    repo.find_all_by_ids_for_user.return_value = [n1, n2]

    svc = _make_service(repo=repo)
    # Mark n1 as read (changes), mark n2 as True (no change)
    result = await svc.bulk_set_read_status(
        user_id=10,
        updates=[(1, True), (2, True)],
    )

    assert result == [1]
    assert n1.read is True
    repo.save_all.assert_awaited_once_with([n1, n2])


@pytest.mark.anyio
async def test_bulk_set_read_status_no_actual_changes():
    repo = AsyncMock()
    n1 = _notification(id=1, read=True)
    repo.find_all_by_ids_for_user.return_value = [n1]

    svc = _make_service(repo=repo)
    # n1 is already True, requesting True => no change
    result = await svc.bulk_set_read_status(
        user_id=10,
        updates=[(1, True)],
    )

    assert result == []
    repo.save_all.assert_not_awaited()


@pytest.mark.anyio
async def test_bulk_set_read_status_notification_with_none_id():
    repo = AsyncMock()
    n_none = _notification(id=None, read=False)
    n_valid = _notification(id=2, read=True)
    repo.find_all_by_ids_for_user.return_value = [n_none, n_valid]

    svc = _make_service(repo=repo)
    result = await svc.bulk_set_read_status(
        user_id=10,
        updates=[(2, False)],
    )

    # n_none is skipped, n_valid changes from True -> False
    assert result == [2]
    assert n_valid.read is False
    repo.save_all.assert_awaited_once()


@pytest.mark.anyio
async def test_bulk_set_read_status_id_not_in_desired_map():
    """A notification returned by repo that was not in the updates list."""
    repo = AsyncMock()
    # Repo returns notification id=3 even though we only asked about id=1
    n1 = _notification(id=1, read=False)
    n3 = _notification(id=3, read=False)
    repo.find_all_by_ids_for_user.return_value = [n1, n3]

    svc = _make_service(repo=repo)
    result = await svc.bulk_set_read_status(
        user_id=10,
        updates=[(1, True)],
    )

    # n3 is not in desired_map so desired is None, skipped
    assert result == [1]
    assert n1.read is True
    assert n3.read is False  # unchanged


# ---------------------------------------------------------------------------
# delete_notification_for_current_user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_notification_success():
    repo = AsyncMock()
    repo.soft_delete_for_user.return_value = True

    svc = _make_service(repo=repo)
    result = await svc.delete_notification_for_current_user(user_id=10, notification_id=5)

    repo.soft_delete_for_user.assert_awaited_once_with(user_id=10, notification_id=5)
    assert result is True


@pytest.mark.anyio
async def test_delete_notification_not_found():
    repo = AsyncMock()
    repo.soft_delete_for_user.return_value = False

    svc = _make_service(repo=repo)
    result = await svc.delete_notification_for_current_user(user_id=10, notification_id=999)

    assert result is False


# ---------------------------------------------------------------------------
# resolve_entities_from_metadata
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_entities_empty_metadata():
    svc = _make_service()
    result = await svc.resolve_entities_from_metadata([])

    assert result == {}


@pytest.mark.anyio
async def test_resolve_entities_no_entity_pointers():
    svc = _make_service()
    result = await svc.resolve_entities_from_metadata([{"key": "plain_value"}])

    assert result == {}


@pytest.mark.anyio
async def test_resolve_entities_with_pointers():
    team_info = ResolvedEntityInfoDTO(id="42", type="team", name="Alpha Team")
    resolver = _FakeResolver("team", {"42": team_info})

    svc = _make_service(resolvers=[resolver])
    result = await svc.resolve_entities_from_metadata([
        {"actor": {"type": "team", "id": "42"}},
    ])

    assert "actor" in result
    assert result["actor"] is team_info


@pytest.mark.anyio
async def test_resolve_entities_missing_resolver():
    """When no resolver exists for an entity type, the pointer key still appears with None."""
    svc = _make_service(resolvers=[])
    result = await svc.resolve_entities_from_metadata([
        {"actor": {"type": "unknown_type", "id": "1"}},
    ])

    # The pointer is collected but no resolver handles "unknown_type",
    # so resolved_by_type won't have "unknown_type" and flattened[path] = entity_map.get(id) = None
    assert "actor" in result
    assert result["actor"] is None


@pytest.mark.anyio
async def test_resolve_entities_nested_mapping():
    user_info = ResolvedEntityInfoDTO(id="7", type="user", name="Alice")
    resolver = _FakeResolver("user", {"7": user_info})

    svc = _make_service(resolvers=[resolver])
    result = await svc.resolve_entities_from_metadata([
        {"deep": {"nested": {"type": "user", "id": "7"}}},
    ])

    assert "deep.nested" in result
    assert result["deep.nested"] is user_info


@pytest.mark.anyio
async def test_resolve_entities_in_sequence():
    user_info = ResolvedEntityInfoDTO(id="1", type="user", name="Bob")
    resolver = _FakeResolver("user", {"1": user_info})

    svc = _make_service(resolvers=[resolver])
    result = await svc.resolve_entities_from_metadata([
        {"actors": [{"type": "user", "id": "1"}]},
    ])

    assert "actors[0]" in result
    assert result["actors[0]"] is user_info


@pytest.mark.anyio
async def test_resolve_entities_multiple_types():
    team_info = ResolvedEntityInfoDTO(id="10", type="team", name="Team A")
    user_info = ResolvedEntityInfoDTO(id="20", type="user", name="Alice")
    team_resolver = _FakeResolver("team", {"10": team_info})
    user_resolver = _FakeResolver("user", {"20": user_info})

    svc = _make_service(resolvers=[team_resolver, user_resolver])
    result = await svc.resolve_entities_from_metadata([
        {
            "team": {"type": "team", "id": "10"},
            "user": {"type": "user", "id": "20"},
        },
    ])

    assert result["team"] is team_info
    assert result["user"] is user_info


@pytest.mark.anyio
async def test_resolve_entities_deduplicates_ids():
    """Same entity id appearing twice should only be resolved once per type."""
    call_count = 0
    original_user_info = ResolvedEntityInfoDTO(id="5", type="user", name="Eve")

    class _CountingResolver:
        def supported_entity_type(self):
            return "user"

        async def resolve(self, entity_ids):
            nonlocal call_count
            call_count += 1
            return {eid: original_user_info for eid in entity_ids}

    svc = _make_service(resolvers=[_CountingResolver()])
    result = await svc.resolve_entities_from_metadata([
        {"a": {"type": "user", "id": "5"}},
        {"b": {"type": "user", "id": "5"}},
    ])

    assert call_count == 1
    assert result["a"] is original_user_info
    assert result["b"] is original_user_info


@pytest.mark.anyio
async def test_resolve_entities_unresolved_entity():
    """Resolver returns None for an unknown entity id."""
    resolver = _FakeResolver("user", {})  # resolves nothing

    svc = _make_service(resolvers=[resolver])
    result = await svc.resolve_entities_from_metadata([
        {"actor": {"type": "user", "id": "999"}},
    ])

    assert result["actor"] is None


# ---------------------------------------------------------------------------
# _collect_entity_pointers -- tested indirectly via resolve_entities
# but also test edge cases for plain values
# ---------------------------------------------------------------------------


def test_collect_entity_pointers_plain_string():
    svc = _make_service()
    output = []
    svc._collect_entity_pointers("hello", path="key", output=output)
    assert output == []


def test_collect_entity_pointers_mapping_without_type_id():
    svc = _make_service()
    output = []
    svc._collect_entity_pointers({"foo": "bar"}, path="key", output=output)
    assert output == []


def test_collect_entity_pointers_mapping_with_type_id():
    svc = _make_service()
    output = []
    svc._collect_entity_pointers(
        {"type": "team", "id": "42", "extra": "data"},
        path="root",
        output=output,
    )
    assert len(output) == 1
    assert output[0].path == "root"
    assert output[0].type == "team"
    assert output[0].id == "42"


def test_collect_entity_pointers_non_string_type():
    """type must be a string for it to be recognized as an entity pointer."""
    svc = _make_service()
    output = []
    svc._collect_entity_pointers({"type": 123, "id": "42"}, path="root", output=output)
    assert len(output) == 0


def test_collect_entity_pointers_non_string_id():
    """id must be a string for it to be recognized as an entity pointer."""
    svc = _make_service()
    output = []
    svc._collect_entity_pointers({"type": "team", "id": 42}, path="root", output=output)
    assert len(output) == 0


def test_collect_entity_pointers_nested_sequence():
    svc = _make_service()
    output = []
    svc._collect_entity_pointers(
        [{"type": "user", "id": "1"}, "plain", {"type": "team", "id": "2"}],
        path="items",
        output=output,
    )
    assert len(output) == 2
    assert output[0].path == "items[0]"
    assert output[1].path == "items[2]"


def test_collect_entity_pointers_bytes_not_iterated():
    """bytes and bytearray should not be iterated as sequences."""
    svc = _make_service()
    output = []
    svc._collect_entity_pointers(b"hello", path="data", output=output)
    assert output == []


def test_collect_entity_pointers_bytearray_not_iterated():
    svc = _make_service()
    output = []
    svc._collect_entity_pointers(bytearray(b"hello"), path="data", output=output)
    assert output == []


def test_collect_entity_pointers_nested_map_in_sequence():
    svc = _make_service()
    output = []
    svc._collect_entity_pointers(
        [{"nested": {"type": "user", "id": "9"}}],
        path="list",
        output=output,
    )
    assert len(output) == 1
    assert output[0].path == "list[0].nested"


# ---------------------------------------------------------------------------
# build_notification_dto
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_notification_dto_with_dict_metadata():
    team_info = ResolvedEntityInfoDTO(id="42", type="team", name="Alpha")
    resolver = _FakeResolver("team", {"42": team_info})

    svc = _make_service(resolvers=[resolver])
    notif = _notification(
        id=1,
        type=NotificationType.MENTION,
        read=False,
        metadata_payload={"actor": {"type": "team", "id": "42"}, "text": "hello"},
        created_at=NOW,
    )

    dto = await svc.build_notification_dto(notif)

    assert dto.id == 1
    assert dto.type == "MENTION"
    assert dto.read is False
    assert dto.createdAt == int(NOW.timestamp() * 1000)
    assert "actor" in dto.entities
    assert dto.entities["actor"] is team_info
    # "text" is a plain value so it should appear in contextMetadata
    assert dto.contextMetadata.get("text") == "hello"


@pytest.mark.anyio
async def test_build_notification_dto_with_non_dict_metadata():
    svc = _make_service()
    notif = _notification(id=2, metadata_payload="not_a_dict")

    dto = await svc.build_notification_dto(notif)

    assert dto.entities == {}
    assert dto.contextMetadata == {}


@pytest.mark.anyio
async def test_build_notification_dto_with_none_metadata():
    svc = _make_service()
    notif = _notification(id=3, metadata_payload=None)

    dto = await svc.build_notification_dto(notif)

    assert dto.entities == {}
    assert dto.contextMetadata == {}


@pytest.mark.anyio
async def test_build_notification_dto_no_metadata_attr():
    """When the notification has no metadata_payload attribute at all."""
    svc = _make_service()
    notif = SimpleNamespace(
        id=4,
        type=NotificationType.REPLY,
        read=True,
        created_at=NOW,
    )

    dto = await svc.build_notification_dto(notif)

    assert dto.entities == {}
    assert dto.contextMetadata == {}


@pytest.mark.anyio
async def test_build_notification_dto_empty_dict_metadata():
    svc = _make_service()
    notif = _notification(id=5, metadata_payload={})

    dto = await svc.build_notification_dto(notif)

    # Empty metadata_map is falsy, so entities resolution is skipped
    assert dto.entities == {}
    assert dto.contextMetadata == {}
