"""Unit tests for app.domain.notification.dto.NotificationDTO."""

from datetime import datetime
from types import SimpleNamespace

from app.domain.notification.dto import NotificationDTO, ResolvedEntityInfoDTO
from app.domain.notification.models import NotificationType

NOW = datetime(2025, 6, 1, 12, 0, 0)


def _notification(**overrides):
    defaults = {
        "id": 1,
        "type": NotificationType.MENTION,
        "read": False,
        "created_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestNotificationDTO:
    def test_from_notification_basic(self):
        n = _notification()
        metadata_map = {"text": "Hello"}
        resolved = {}

        dto = NotificationDTO.from_notification(n, metadata_map, resolved)
        assert dto.id == 1
        assert dto.type == "MENTION"
        assert dto.read is False
        assert dto.createdAt == int(NOW.timestamp() * 1000)
        assert dto.contextMetadata == {"text": "Hello"}

    def test_from_notification_strips_entity_nodes(self):
        n = _notification()
        metadata_map = {
            "actor": {"id": "1", "type": "user"},
            "target": {"id": "2", "type": "discussion"},
            "text": "Some text",
        }
        resolved = {
            "actor": ResolvedEntityInfoDTO(id="1", type="user", name="Alice"),
            "target": ResolvedEntityInfoDTO(id="2", type="discussion", name="Thread"),
        }

        dto = NotificationDTO.from_notification(n, metadata_map, resolved)
        # Entity nodes should be stripped from contextMetadata
        assert "actor" not in dto.contextMetadata
        assert "target" not in dto.contextMetadata
        assert dto.contextMetadata["text"] == "Some text"
        assert "actor" in dto.entities
        assert "target" in dto.entities

    def test_from_notification_no_created_at(self):
        n = _notification(created_at=None)
        dto = NotificationDTO.from_notification(n, {}, {})
        assert dto.createdAt == 0

    def test_from_notification_string_type(self):
        n = _notification(type="CUSTOM_TYPE")
        dto = NotificationDTO.from_notification(n, {}, {})
        assert dto.type == "CUSTOM_TYPE"

    def test_strip_entity_nodes_simple_value(self):
        assert NotificationDTO._strip_entity_nodes("hello") == "hello"
        assert NotificationDTO._strip_entity_nodes(42) == 42
        assert NotificationDTO._strip_entity_nodes(True) is True

    def test_strip_entity_nodes_entity_dict(self):
        entity = {"id": "1", "type": "user"}
        assert NotificationDTO._strip_entity_nodes(entity) is None

    def test_strip_entity_nodes_non_entity_dict(self):
        data = {"key": "value", "count": 5}
        assert NotificationDTO._strip_entity_nodes(data) == {"key": "value", "count": 5}

    def test_strip_entity_nodes_nested_dict_with_entity(self):
        data = {
            "actor": {"id": "1", "type": "user"},
            "text": "hello",
        }
        result = NotificationDTO._strip_entity_nodes(data)
        assert result == {"text": "hello"}

    def test_strip_entity_nodes_list(self):
        data = ["hello", "world"]
        result = NotificationDTO._strip_entity_nodes(data)
        assert result == ["hello", "world"]

    def test_strip_entity_nodes_list_with_entities(self):
        data = [
            {"id": "1", "type": "user"},
            "keep_this",
            {"id": "2", "type": "team"},
        ]
        result = NotificationDTO._strip_entity_nodes(data)
        assert result == ["keep_this"]

    def test_strip_entity_nodes_bytes(self):
        # bytes should not be treated as sequence
        assert NotificationDTO._strip_entity_nodes(b"bytes") == b"bytes"

    def test_strip_entity_nodes_id_not_str(self):
        # id is int, not str -> not an entity
        data = {"id": 1, "type": "user"}
        result = NotificationDTO._strip_entity_nodes(data)
        assert result == {"id": 1, "type": "user"}

    def test_strip_entity_nodes_type_not_str(self):
        # type is int -> not an entity
        data = {"id": "1", "type": 42}
        result = NotificationDTO._strip_entity_nodes(data)
        assert result == {"id": "1", "type": 42}

    def test_from_notification_nested_entity_in_metadata(self):
        n = _notification()
        metadata_map = {
            "wrapper": {
                "actor": {"id": "1", "type": "user"},
                "text": "nested",
            }
        }

        dto = NotificationDTO.from_notification(n, metadata_map, {})
        # The wrapper should remain but with actor stripped
        assert dto.contextMetadata == {"wrapper": {"text": "nested"}}


class TestResolvedEntityInfoDTO:
    def test_basic_fields(self):
        dto = ResolvedEntityInfoDTO(
            id="1",
            type="user",
            name="Alice",
            url="/users/1",
            avatarUrl="https://cdn.example.com/avatars/1",
            status=None,
        )
        assert dto.id == "1"
        assert dto.type == "user"
        assert dto.name == "Alice"
        assert dto.url == "/users/1"
        assert dto.avatarUrl == "https://cdn.example.com/avatars/1"
        assert dto.status is None

    def test_optional_fields_default(self):
        dto = ResolvedEntityInfoDTO(id="1", type="team", name="Alpha")
        assert dto.url is None
        assert dto.avatarUrl is None
        assert dto.status is None
