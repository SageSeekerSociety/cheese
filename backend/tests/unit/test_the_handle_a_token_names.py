"""Which handle an access token names.

``app.auth.caller.handle_named_by`` is the one answer to that question — the
workspace routes and the live-view gate both read it — and what it answers IS
an authorization key: the roster is keyed by handle, so a wrong answer is not a
wrong rendering but a permission granted or taken away.

Worth pinning without a database, then: which of the two the token says, and
what is asked of the row when it says the id.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import caller
from app.common.auth import AccessClaims, create_access_token, verify_access_token


def _claims(*args: object, **kwargs: object) -> AccessClaims:
    claims = verify_access_token(create_access_token(*args, **kwargs))  # type: ignore[arg-type]
    assert claims is not None
    return claims


@pytest.mark.anyio
async def test_an_all_digit_name_is_not_looked_up_as_a_user_id(monkeypatch) -> None:
    """The token says "2026201782", and that IS the handle.

    ``_USERNAME_RE`` allows an all-digit username, so this is a name a person
    can have — and the one a caller who read the digits as a user id, found
    nobody, and answered 「登录状态无效或已过期」 had. Nothing here needs the
    row: the claim is the answer, which is also why no lookup is allowed.
    """
    monkeypatch.setattr(
        caller.UserRepository,
        "get_by_id",
        AsyncMock(side_effect=AssertionError("a stated name needs no lookup")),
    )
    session = cast(AsyncSession, object())

    handle = await caller.handle_named_by(session, _claims(7, "2026201782"))

    assert handle == "2026201782"


@pytest.mark.anyio
async def test_a_token_that_states_no_handle_is_resolved_through_its_user_id(
    monkeypatch,
) -> None:
    """An older token's ``handle`` is its ``sub`` — the int user PK.

    The name has to come off the row (``User`` has no ``handle`` column; the
    username is what the roster holds), read through the id the token carries.
    """
    repo = AsyncMock()
    repo.get_by_id.return_value = SimpleNamespace(username="alice")
    monkeypatch.setattr(caller, "UserRepository", lambda session: repo)
    session = cast(AsyncSession, object())

    handle = await caller.handle_named_by(session, _claims(7))

    assert handle == "alice"
    repo.get_by_id.assert_awaited_once_with(7)


@pytest.mark.anyio
async def test_an_id_that_names_nobody_stays_a_handle_nobody_holds(monkeypatch) -> None:
    """Authorization must still deny — and must not read as an anonymous caller.

    Returning None here would make the workspace file routes answer 401 for a
    credential that was in fact readable; keeping the value leaves the caller
    unauthorized, which is what a token whose user is gone should get.
    """
    repo = AsyncMock()
    repo.get_by_id.return_value = None
    monkeypatch.setattr(caller, "UserRepository", lambda session: repo)
    session = cast(AsyncSession, object())

    handle = await caller.handle_named_by(session, _claims(7))

    assert handle == "7"
