"""The discussion and user-auth service factories are defined once, in deps.

Routes that build these services must import the factory from
``app.api.deps`` instead of keeping a local copy: a copy drifts from the
canonical wiring and escapes ``dependency_overrides`` keyed on the deps one.
"""

from app.api import deps
from app.api.routes import answers, comments, questions


def test_discussion_service_factory_comes_from_deps() -> None:
    assert questions.get_discussion_service is deps.get_discussion_service
    assert answers.get_discussion_service is deps.get_discussion_service


def test_user_auth_service_factory_comes_from_deps() -> None:
    assert comments.get_user_auth_service is deps.get_user_auth_service


def test_factories_are_exported_from_deps() -> None:
    assert "get_discussion_service" in deps.__all__
    assert "get_user_auth_service" in deps.__all__
