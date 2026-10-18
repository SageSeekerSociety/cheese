from unittest.mock import AsyncMock

import pytest

from app.core.errors import NotFoundError
from app.domain.questions.lookup import ensure_question_exists


@pytest.mark.anyio
async def test_a_missing_question_is_a_404_that_names_it():
    question_repo = AsyncMock()
    question_repo.get_by_id.return_value = None

    with pytest.raises(NotFoundError, match="Question not found") as raised:
        await ensure_question_exists(question_repo, 999)

    assert raised.value.data == {"id": 999}


@pytest.mark.anyio
async def test_an_existing_question_passes():
    question_repo = AsyncMock()
    question_repo.get_by_id.return_value = object()

    await ensure_question_exists(question_repo, 10)

    question_repo.get_by_id.assert_awaited_once_with(10)
