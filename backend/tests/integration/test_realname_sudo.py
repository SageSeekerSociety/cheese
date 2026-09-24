"""A real name and student ID are read only by their owner. They are shown
unmasked, and changed, only after the owner re-authenticates for that
operation; the masked form needs the owner's session alone."""

import pytest
from fastapi.testclient import TestClient

from app.common.auth import create_access_token
from tests.integration.conftest import UserCreator

IDENTITY = {
    "realName": "张三丰",
    "studentId": "2025000123",
    "grade": "2025",
    "major": "计算机科学与技术",
    "className": "1",
}


class _Owner:
    def __init__(self, user_creator: UserCreator) -> None:
        created = user_creator.create_user()
        self.id = created.user_id
        self._password = created.password
        self.headers = {
            "Authorization": "Bearer "
            + create_access_token(created.user_id, handle=created.username)
        }

    def sudo_ticket(self, client: TestClient, purpose: str) -> str:
        """A ticket got the way a client gets one: by re-entering the password."""
        resp = client.post(
            "/users/auth/sudo",
            headers=self.headers,
            json={
                "method": "password",
                "credentials": {"password": self._password},
                "purpose": purpose,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["sudoTicket"]

    def read(self, client: TestClient, *, precise: bool, ticket: str | None = None):
        params: dict[str, str] = {"precise": "true" if precise else "false"}
        if ticket is not None:
            params["sudoTicket"] = ticket
        return client.get(
            f"/users/{self.id}/identity", headers=self.headers, params=params
        )

    def patch(self, client: TestClient, body: dict, ticket: str | None):
        payload = body if ticket is None else {**body, "sudoTicket": ticket}
        return client.patch(
            f"/users/{self.id}/identity", headers=self.headers, json=payload
        )

    def put(self, client: TestClient, body: dict, ticket: str | None):
        payload = body if ticket is None else {**body, "sudoTicket": ticket}
        return client.put(
            f"/users/{self.id}/identity", headers=self.headers, json=payload
        )


@pytest.fixture
def owner(user_client, api_client) -> _Owner:
    return _Owner(user_client)


@pytest.fixture
def registered(owner: _Owner, api_client: TestClient) -> _Owner:
    ticket = owner.sudo_ticket(api_client, "realname:update")
    resp = owner.put(api_client, IDENTITY, ticket)
    assert resp.status_code == 200, resp.text
    return owner


def _refused(resp) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["name"] == "SudoRequiredError"


class TestReading:
    def test_the_masked_form_needs_no_ticket(
        self, registered: _Owner, api_client: TestClient
    ):
        resp = registered.read(api_client, precise=False)

        assert resp.status_code == 200, resp.text
        identity = resp.json()["data"]["identity"]
        assert identity["realName"] != IDENTITY["realName"]
        assert identity["studentId"] != IDENTITY["studentId"]

    def test_the_unmasked_form_is_refused_without_a_ticket(
        self, registered: _Owner, api_client: TestClient
    ):
        _refused(registered.read(api_client, precise=True))

    def test_the_unmasked_form_is_shown_with_a_view_ticket(
        self, registered: _Owner, api_client: TestClient
    ):
        ticket = registered.sudo_ticket(api_client, "realname:view")

        resp = registered.read(api_client, precise=True, ticket=ticket)

        assert resp.status_code == 200, resp.text
        identity = resp.json()["data"]["identity"]
        assert identity["realName"] == IDENTITY["realName"]
        assert identity["studentId"] == IDENTITY["studentId"]

    def test_a_ticket_for_another_operation_is_refused(
        self, registered: _Owner, api_client: TestClient
    ):
        ticket = registered.sudo_ticket(api_client, "realname:update")

        _refused(registered.read(api_client, precise=True, ticket=ticket))


class TestSomeoneElse:
    @pytest.mark.parametrize("has_record", [True, False])
    def test_another_user_is_refused_the_masked_form(
        self,
        has_record: bool,
        owner: _Owner,
        user_client: UserCreator,
        api_client: TestClient,
    ):
        if has_record:
            ticket = owner.sudo_ticket(api_client, "realname:update")
            assert owner.put(api_client, IDENTITY, ticket).status_code == 200
        stranger = _Owner(user_client)

        resp = api_client.get(
            f"/users/{owner.id}/identity",
            headers=stranger.headers,
            params={"precise": "false"},
        )

        # Refused the same way whether or not a record exists, and as a
        # permission refusal, not as a prompt to re-authenticate.
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"]["name"] == "ForbiddenError"
        assert "identity" not in (body.get("data") or {})


class TestChanging:
    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_a_change_is_refused_without_a_ticket(
        self, registered: _Owner, api_client: TestClient, method: str
    ):
        change = {**IDENTITY, "major": "数学"}

        _refused(getattr(registered, method)(api_client, change, None))
        _assert_major(registered, api_client, IDENTITY["major"])

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_a_change_is_accepted_with_an_update_ticket(
        self, registered: _Owner, api_client: TestClient, method: str
    ):
        ticket = registered.sudo_ticket(api_client, "realname:update")
        change = {**IDENTITY, "major": "数学"}

        resp = getattr(registered, method)(api_client, change, ticket)

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["identity"]["major"] == "数学"
        _assert_major(registered, api_client, "数学")

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_a_ticket_for_another_operation_is_refused(
        self, registered: _Owner, api_client: TestClient, method: str
    ):
        ticket = registered.sudo_ticket(api_client, "realname:view")
        change = {**IDENTITY, "major": "数学"}

        _refused(getattr(registered, method)(api_client, change, ticket))
        _assert_major(registered, api_client, IDENTITY["major"])


def _assert_major(owner: _Owner, client: TestClient, major: str) -> None:
    resp = owner.read(client, precise=False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["identity"]["major"] == major
