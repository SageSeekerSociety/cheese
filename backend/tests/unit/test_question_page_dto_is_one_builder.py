"""One row comes back the same from every question page, built once.

``/users/{id}/favorites/questions``, ``/users/{id}/follow/questions`` and
``/users/{id}/questions`` all describe the same list of questions, and none of
them may carry the question body. Each handler used to hand-copy the mapping,
so a field could drift in one place and not the others. These pin all of them
to the domain's one builder: the handlers ask it for the DTO, and the same row
through any two handlers is the same DTO, field for field.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes.users import directory, preferences
from app.auth.core import AuthUserInfo
from app.domain.questions import services as question_services

PAGE_DTO_KEYS = {
    "id",
    "title",
    "content",
    "type",
    "groupId",
    "bounty",
    "acceptedAnswerId",
    "createdBy",
    "createdAt",
    "updatedAt",
    "topicIds",
}


def _row():
    at = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    return SimpleNamespace(
        id=41,
        title="Where do I start?",
        content="a body the page endpoints must not return",
        type="single",
        group_id=7,
        bounty=5,
        accepted_answer_id=3,
        created_by_id=9,
        created_at=at,
        updated_at=at,
    )


def _stub_question_page(monkeypatch, module):
    """Stand each repository a page handler builds on the same fixed row."""
    row = _row()

    questions = MagicMock()
    questions.list_followed = AsyncMock(return_value=([row], 1))
    questions.list_by_user = AsyncMock(return_value=([row], 1))
    monkeypatch.setattr(module, "QuestionRepository", MagicMock(return_value=questions))

    topics = MagicMock()
    topics.list_topic_ids = AsyncMock(return_value={row.id: [2, 4]})
    monkeypatch.setattr(
        module, "QuestionTopicRepository", MagicMock(return_value=topics)
    )

    users = MagicMock()
    users.get_by_id = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(module, "UserRepository", MagicMock(return_value=users))
    return row


def _watch_the_builder(monkeypatch, module):
    """Record every question the module hands to the domain's builder."""
    seen: list[tuple[int, bool]] = []
    build = question_services.question_to_dto

    def spy(question, *, include_content=True):
        seen.append((question.id, include_content))
        return build(question, include_content=include_content)

    monkeypatch.setattr(module, "question_to_dto", spy)
    return seen


async def _page(handler, auth_user):
    return await handler(
        user_id=1,
        page_start=None,
        page_size=20,
        auth_user=auth_user,
        db=object(),
    )


@pytest.mark.anyio
async def test_every_question_page_builds_its_dto_through_the_domain(monkeypatch):
    _stub_question_page(monkeypatch, preferences)
    _stub_question_page(monkeypatch, directory)
    favorites_built = _watch_the_builder(monkeypatch, preferences)
    followed_built = _watch_the_builder(monkeypatch, directory)
    auth_user = AuthUserInfo(user_id=1)

    await _page(preferences.get_user_favorite_questions, auth_user)
    await _page(directory.get_user_followed_questions, auth_user)
    await _page(directory.get_user_questions, auth_user)

    assert favorites_built == [(41, False)]
    assert followed_built == [(41, False), (41, False)]


@pytest.mark.anyio
async def test_one_row_is_the_same_dto_from_every_question_page(monkeypatch):
    row = _stub_question_page(monkeypatch, preferences)
    _stub_question_page(monkeypatch, directory)
    auth_user = AuthUserInfo(user_id=1)

    favorites = await _page(preferences.get_user_favorite_questions, auth_user)
    followed = await _page(directory.get_user_followed_questions, auth_user)
    asked = await _page(directory.get_user_questions, auth_user)

    favorites_dto = favorites["data"]["questions"][0]
    followed_dto = followed["data"]["questions"][0]
    asked_dto = asked["data"]["questions"][0]

    assert favorites_dto["id"] == row.id
    assert favorites_dto == followed_dto == asked_dto
    assert set(favorites_dto) == PAGE_DTO_KEYS
    assert favorites_dto["content"] is None
    assert favorites_dto["topicIds"] == [2, 4]
    assert favorites_dto["createdAt"] == 1792314000000
