"""The password a new account registers with.

The web client submits the password itself, so the server's rule is the one
that decides: any symbol the client's rule accepts must be accepted here too,
or a password the sign-up form allowed would be refused only after the email
code had been sent.
"""

import uuid

import pytest

from tests.integration.test_account_uniqueness import _registration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("password", "status"),
    [
        ("pass word1", 200),
        ("password~1", 200),
        ("password`1", 200),
        ("password!1", 200),
        ("password12", 422),
        ("password中文", 422),
        ("!!!!1234", 422),
        ("pa!1", 422),
        ("x!" * 40, 400),
    ],
    ids=[
        "space",
        "tilde",
        "backtick",
        "exclamation",
        "no-symbol",
        "non-ascii-only",
        "no-letter",
        "too-short",
        "over-72-bytes",
    ],
)
async def test_registration_password_rule(client, password: str, status: int):
    suffix = uuid.uuid4().hex[:8]
    body = await _registration(f"pw_{suffix}", f"pw-{suffix}@example.com")
    body["password"] = password

    resp = client.post("/users", json=body)
    assert resp.status_code == status, resp.text

    if status == 200:
        login = client.post(
            "/users/auth/login",
            json={"username": body["username"], "password": password},
        )
        assert login.status_code == 200, login.text
