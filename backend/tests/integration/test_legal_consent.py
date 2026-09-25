"""Accounts are created only with a recorded consent, and material changes to
the rules are put to everyone again (#1486)."""

from dataclasses import replace
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.routes.users import _issue_oauth_state_token
from app.domain.legal.documents import DOCUMENTS, LegalVersion
from tests.integration.conftest import CreatedUser
from tests.integration.test_account_uniqueness import _arm_email_code
from tests.support.consent import (
    CURRENT_VERSIONS,
    OAUTH_CONSENT_FORM,
    SIGNUP_CONSENT,
)


def _signup(api_client: TestClient, portal, name: str, **extra):
    payload = {
        "username": name,
        "nickname": name,
        "email": f"{name}@consent-test.example.com",
        "password": "TestPassword123!",
        **extra,
    }
    payload["emailCode"] = portal.call(_arm_email_code, payload["email"])
    return api_client.post("/users", json=payload)


def _pending(api_client: TestClient, token: str) -> list[str]:
    r = api_client.get(
        "/users/me/consents", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    return [d["document"] for d in r.json()["data"]["pending"]]


def _account_exists(db_session, portal, username: str) -> bool:
    from app.domain.user.repositories import UserRepository

    async def lookup() -> bool:
        return await UserRepository(db_session).get_by_username(username) is not None

    return portal.call(lookup)


class TestSignup:
    def test_signup_without_consent_creates_no_account(
        self, api_client: TestClient, db_session, _portal
    ):
        r = _signup(api_client, _portal, "noconsent1")

        assert r.status_code == 422, r.text
        assert "同意" in r.json()["message"]
        assert not _account_exists(db_session, _portal, "noconsent1")

    def test_signup_against_an_outdated_version_is_sent_back(
        self, api_client: TestClient, db_session, _portal
    ):
        stale = {
            "documents": {**CURRENT_VERSIONS, "terms": "0.9"},
            "method": "checkbox",
        }

        r = _signup(api_client, _portal, "staleconsent1", consent=stale)

        assert r.status_code == 422, r.text
        assert "更新" in r.json()["message"]
        assert not _account_exists(db_session, _portal, "staleconsent1")

    def test_signup_missing_one_document_is_refused(
        self, api_client: TestClient, _portal
    ):
        only_terms = {
            "documents": {"terms": CURRENT_VERSIONS["terms"]},
            "method": "checkbox",
        }

        r = _signup(api_client, _portal, "halfconsent1", consent=only_terms)

        assert r.status_code == 422, r.text

    @pytest.mark.parametrize("method", ["checkbox", "dialog"])
    def test_a_consenting_signup_is_not_asked_again(
        self, api_client: TestClient, _portal, method: str
    ):
        r = _signup(
            api_client,
            _portal,
            f"consented{method}",
            consent={**SIGNUP_CONSENT, "method": method},
        )

        assert r.status_code == 200, r.text
        assert _pending(api_client, r.json()["data"]["accessToken"]) == []


class TestOAuthSignup:
    def _create(self, api_client: TestClient, portal, uid: str, **consent):
        token = portal.call(
            _issue_oauth_state_token,
            "ruc",
            {
                "id": uid,
                "name": "Prov",
                "preferredUsername": uid,
                "verifiedEmail": f"{uid}@example.com",
            },
        )
        return api_client.post(
            "/users/oauth/create",
            data={
                "stateToken": token,
                "username": f"oauth_{uid}",
                "nickname": "prov",
                "passwordMode": "none",
                **consent,
            },
            follow_redirects=False,
        )

    def test_oauth_signup_without_consent_creates_no_account(
        self, api_client: TestClient, db_session, _portal
    ):
        resp = self._create(api_client, _portal, "noconsent")

        location = resp.headers["location"]
        assert parse_qs(urlparse(location).query)["error_code"] == ["CONSENT_REQUIRED"]
        assert not _account_exists(db_session, _portal, "oauth_noconsent")

    def test_a_consenting_oauth_signup_is_not_asked_again(
        self, api_client: TestClient, _portal
    ):
        resp = self._create(api_client, _portal, "consented", **OAUTH_CONSENT_FORM)

        params = parse_qs(urlparse(resp.headers["location"]).query)
        assert params["created"] == ["true"]
        # The landing page signs in with the cookie the redirect set.
        refreshed = api_client.post(
            "/users/auth/refresh-token",
            headers={"Cookie": f"cheese_refresh={resp.cookies['cheese_refresh']}"},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert _pending(api_client, refreshed.json()["data"]["accessToken"]) == []


class TestReacceptance:
    def test_an_account_that_never_consented_is_asked_for_both(
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        assert sorted(_pending(api_client, token_of(authenticated_user))) == [
            "privacy",
            "terms",
        ]

    def test_accepting_clears_what_is_pending(
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        headers = {"Authorization": f"Bearer {token_of(authenticated_user)}"}

        r = api_client.post(
            "/users/me/consents", json={"documents": CURRENT_VERSIONS}, headers=headers
        )

        assert r.status_code == 200, r.text
        assert _pending(api_client, token_of(authenticated_user)) == []

    def test_the_recorded_address_is_not_one_the_client_wrote(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        db_session,
        _portal,
    ):
        """A consent row is evidence of who agreed from where; a forwarded
        address from a peer that is not one of our proxies proves nothing."""
        from sqlalchemy import select

        from app.domain.legal.models import UserConsent

        headers = {
            "Authorization": f"Bearer {token_of(authenticated_user)}",
            "X-Forwarded-For": "6.6.6.6",
        }
        r = api_client.post(
            "/users/me/consents", json={"documents": CURRENT_VERSIONS}, headers=headers
        )
        assert r.status_code == 200, r.text

        async def recorded() -> set[str]:
            rows = await db_session.execute(
                select(UserConsent.ip).where(
                    UserConsent.user_id == authenticated_user.user_id
                )
            )
            return set(rows.scalars())

        # TestClient's peer, which is not a trusted proxy.
        assert _portal.call(recorded) == {"testclient"}

    def test_accepting_only_part_of_what_is_pending_is_refused(
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        headers = {"Authorization": f"Bearer {token_of(authenticated_user)}"}

        r = api_client.post(
            "/users/me/consents",
            json={"documents": {"terms": CURRENT_VERSIONS["terms"]}},
            headers=headers,
        )

        assert r.status_code == 422, r.text
        assert sorted(_pending(api_client, token_of(authenticated_user))) == [
            "privacy",
            "terms",
        ]

    def test_accepting_an_outdated_version_is_refused(
        self, api_client: TestClient, authenticated_user: CreatedUser
    ):
        headers = {"Authorization": f"Bearer {token_of(authenticated_user)}"}

        r = api_client.post(
            "/users/me/consents",
            json={"documents": {**CURRENT_VERSIONS, "privacy": "0.9"}},
            headers=headers,
        )

        assert r.status_code == 422, r.text

    def test_a_material_change_is_put_to_people_who_accepted_before(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        monkeypatch: pytest.MonkeyPatch,
    ):
        token = token_of(authenticated_user)
        api_client.post(
            "/users/me/consents",
            json={"documents": CURRENT_VERSIONS},
            headers={"Authorization": f"Bearer {token}"},
        )

        terms = DOCUMENTS["terms"]
        monkeypatch.setitem(
            DOCUMENTS,
            "terms",
            replace(
                terms,
                versions=(*terms.versions, LegalVersion("9.0", date(2030, 1, 1))),
            ),
        )

        assert _pending(api_client, token) == ["terms"]

    def test_a_wording_fix_is_not_put_to_anyone_again(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        monkeypatch: pytest.MonkeyPatch,
    ):
        token = token_of(authenticated_user)
        api_client.post(
            "/users/me/consents",
            json={"documents": CURRENT_VERSIONS},
            headers={"Authorization": f"Bearer {token}"},
        )

        terms = DOCUMENTS["terms"]
        monkeypatch.setitem(
            DOCUMENTS,
            "terms",
            replace(
                terms,
                versions=(
                    *terms.versions,
                    LegalVersion("9.0", date(2030, 1, 1), material=False),
                ),
            ),
        )

        assert _pending(api_client, token) == []

    def test_pending_needs_a_signed_in_user(self, api_client: TestClient):
        assert api_client.get("/users/me/consents").status_code == 401


def token_of(user: CreatedUser) -> str:
    assert user.token is not None
    return user.token


class TestPublicDocuments:
    def test_documents_are_readable_without_signing_in(self, api_client: TestClient):
        listed = api_client.get("/legal/documents")
        assert listed.status_code == 200, listed.text
        assert {d["document"] for d in listed.json()["data"]["documents"]} == {
            "terms",
            "privacy",
        }

        for key, title in (("terms", "用户协议"), ("privacy", "隐私政策")):
            r = api_client.get(f"/legal/documents/{key}")
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert data["version"] == CURRENT_VERSIONS[key]
            assert title in data["content"]

    def test_a_published_version_stays_readable_by_number(self, api_client: TestClient):
        version = CURRENT_VERSIONS["privacy"]

        r = api_client.get(f"/legal/documents/privacy/versions/{version}")

        assert r.status_code == 200, r.text
        assert (
            r.json()["data"]["content"]
            == (api_client.get("/legal/documents/privacy").json()["data"]["content"])
        )

    def test_every_version_someone_may_have_accepted_stays_readable(
        self, api_client: TestClient
    ):
        for key, doc in DOCUMENTS.items():
            for v in doc.versions:
                r = api_client.get(f"/legal/documents/{key}/versions/{v.version}")
                assert r.status_code == 200, (key, v.version, r.text)
                assert r.json()["data"]["content"].strip()

    def test_unknown_documents_and_versions_are_not_found(self, api_client: TestClient):
        assert api_client.get("/legal/documents/cookies").status_code == 404
        assert api_client.get("/legal/documents/terms/versions/0.1").status_code == 404
