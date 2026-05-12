"""Regression tests: page objects must use camelCase keys.

The frontend Page type expects { pageStart, pageSize, hasMore, nextStart, hasPrev, prevStart }.
These tests verify that materials, groups, and topics pagination responses use camelCase keys
and do NOT contain snake_case equivalents.
"""

import io

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator, unique_int

CAMEL_KEYS = {"pageStart", "pageSize", "hasMore", "nextStart", "hasPrev", "prevStart"}
SNAKE_KEYS = {"page_start", "page_size", "has_more", "next_start", "has_prev", "prev_start"}


def _assert_page_keys_camel(page: dict) -> None:
    """Assert page dict uses camelCase keys only."""
    for key in CAMEL_KEYS:
        assert key in page, f"Missing camelCase key '{key}' in page: {page}"
    for key in SNAKE_KEYS:
        assert key not in page, f"Unexpected snake_case key '{key}' in page: {page}"


class TestMaterialBundlePageCamelCase:
    """Material bundle listing must return camelCase page keys."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.headers = auth_headers
        self.unique = str(unique_int(1000000000, 9999999999))
        # Create a material and two bundles for pagination
        fake_file = io.BytesIO(b"\x89PNG\r\n\x1a\nfake")
        resp = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.jpg", fake_file, "image/jpeg")},
            data={"type": "image"},
        )
        self.material_id = resp.json()["data"]["id"]
        for _ in range(2):
            self.client.post(
                "/material-bundles",
                headers=self.headers,
                json={
                    "title": f"camel_page_test_{self.unique}",
                    "content": "test",
                    "materials": [self.material_id],
                },
            )

    def test_bundles_page_uses_camel_case(self):
        resp = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={"q": self.unique, "page_size": 1, "sort": ""},
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        _assert_page_keys_camel(page)

    def test_bundles_empty_page_uses_camel_case(self):
        resp = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={"q": "nonexistent_keyword_zzzzzz", "sort": ""},
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        _assert_page_keys_camel(page)


class TestGroupMembersPageCamelCase:
    """Group member listing must return camelCase page keys."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.headers = auth_headers
        # Create a group
        resp = self.client.post(
            "/groups",
            headers=self.headers,
            json={
                "name": f"camel_page_group_{unique_int(100000, 999999)}",
                "intro": "testing",
            },
        )
        assert resp.status_code == 201, f"Failed to create group: {resp.text}"
        self.group_id = resp.json()["data"]["group"]["id"]

    def test_members_page_uses_camel_case(self):
        resp = self.client.get(
            f"/groups/{self.group_id}/members",
            headers=self.headers,
            params={"page_size": 10},
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        _assert_page_keys_camel(page)

    def test_members_empty_page_uses_camel_case(self):
        resp = self.client.get(
            f"/groups/{self.group_id}/members",
            headers=self.headers,
            params={"page_size": -1},
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        _assert_page_keys_camel(page)
