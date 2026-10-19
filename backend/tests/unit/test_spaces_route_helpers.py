"""Unit tests for helpers in app/api/routes/spaces.py and analytics wiring."""

import pytest
from fastapi.routing import APIRoute

from app.api import deps, space_deps
from app.api.routes import spaces_analytics
from app.core.errors import BadRequestError


def test_expect_list_with_none_returns_empty():
    from app.api.routes.spaces import _expect_list

    assert _expect_list(None, "taskTemplates") == []


def test_expect_list_with_plain_list_returns_same():
    from app.api.routes.spaces import _expect_list

    items = [{"title": "Hello"}, {"title": "World"}]
    assert _expect_list(items, "taskTemplates") == items


def test_expect_list_with_valid_json_array_string():
    from app.api.routes.spaces import _expect_list

    result = _expect_list('[{"title": "Template"}]', "taskTemplates")
    assert result == [{"title": "Template"}]


def test_expect_list_with_empty_json_array_string():
    from app.api.routes.spaces import _expect_list

    assert _expect_list("[]", "taskTemplates") == []


def test_expect_list_with_invalid_json_string_raises():
    from app.api.routes.spaces import _expect_list

    with pytest.raises(BadRequestError, match="must be a valid JSON array"):
        _expect_list("not-valid-json", "taskTemplates")


def test_expect_list_with_non_array_json_string_raises():
    from app.api.routes.spaces import _expect_list

    with pytest.raises(BadRequestError, match="must be a JSON array"):
        _expect_list('{"key": "value"}', "taskTemplates")


def test_expect_list_with_unexpected_type_raises():
    from app.api.routes.spaces import _expect_list

    with pytest.raises(BadRequestError, match="must be a list or a JSON array string"):
        _expect_list(42, "taskTemplates")  # type: ignore[arg-type]


def test_create_space_visible_task_limit_accepts_null_and_zero():
    from app.api.routes.spaces import CreateSpaceRequest

    assert (
        CreateSpaceRequest(name="s", visibleTaskLimit=None).visible_task_limit is None
    )
    assert CreateSpaceRequest(name="s", visibleTaskLimit=0).visible_task_limit == 0


def test_patch_space_visible_task_limit_rejects_negative():
    from app.api.routes.spaces import PatchSpaceRequest

    with pytest.raises(ValueError):
        PatchSpaceRequest(visibleTaskLimit=-1)


def test_patch_space_visible_task_limit_rejects_string_number():
    from app.api.routes.spaces import PatchSpaceRequest

    with pytest.raises(ValueError):
        PatchSpaceRequest(visibleTaskLimit="1")  # type: ignore[arg-type]


def test_participant_export_uses_the_shared_realname_factory():
    route = next(
        route
        for route in spaces_analytics.router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith("/analytics/participants/export")
    )
    dependency_calls = set()
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        dependency_calls.add(dependant.call)
        pending.extend(dependant.dependencies)

    assert deps.get_user_realname_service in dependency_calls
    assert not hasattr(space_deps, "get_space_user_realname_service")
