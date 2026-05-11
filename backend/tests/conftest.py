import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture(scope="session")
def kotlin_base_url() -> str:
    """
    Base URL of the existing Kotlin backend for contract tests.

    Configure via KOTLIN_BASE_URL env var, e.g.:
      export KOTLIN_BASE_URL=http://localhost:8080
    """
    return os.getenv("KOTLIN_BASE_URL", "http://localhost:8080")


@pytest.fixture(scope="session")
def python_base_url() -> str:
    """
    Base URL of Python backend when running as external service.

    Configure via PYTHON_BASE_URL env var, e.g.:
      export PYTHON_BASE_URL=http://localhost:8000
    """
    return os.getenv("PYTHON_BASE_URL", "http://localhost:8000")


@pytest.fixture()
async def python_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client against the new Python FastAPI app (in-process)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture()
async def kotlin_client(kotlin_base_url: str) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client against the Kotlin backend (external service)."""
    async with AsyncClient(base_url=kotlin_base_url, timeout=30.0) as client:
        yield client


@pytest.fixture()
def test_user_token() -> str:
    """
    JWT token for a test user. Configure via TEST_USER_TOKEN env var.
    Used for authenticated API calls in contract tests.
    """
    return os.getenv("TEST_USER_TOKEN", "")


@pytest.fixture()
def test_user_id() -> str:
    """Test user ID for X-User-Id header (Python temporary auth)."""
    return os.getenv("TEST_USER_ID", "1")


@pytest.fixture()
def auth_headers(test_user_token: str, test_user_id: str) -> dict[str, str]:
    """
    Headers for authenticated requests.
    - For Kotlin: Authorization: Bearer <token>
    - For Python: X-User-Id: <id> (temporary until JWT migration)
    """
    headers: dict[str, str] = {}
    if test_user_token:
        headers["Authorization"] = f"Bearer {test_user_token}"
    headers["X-User-Id"] = test_user_id
    return headers


class ResponseComparator:
    """
    Utility for comparing API responses between Kotlin and Python backends.
    Ignores dynamic fields like timestamps and IDs when comparing structure.
    """

    DYNAMIC_FIELDS = {"id", "createdAt", "updatedAt", "timestamp", "accessToken", "refreshToken"}
    IGNORE_FIELDS: set[str] = set()

    def __init__(
        self,
        ignore_fields: set[str] | None = None,
        dynamic_fields: set[str] | None = None,
    ):
        self.ignore_fields = ignore_fields or self.IGNORE_FIELDS
        self.dynamic_fields = dynamic_fields or self.DYNAMIC_FIELDS

    def compare_structure(self, kotlin_data: Any, python_data: Any, path: str = "") -> list[str]:
        """
        Compare the structure of two responses.
        Returns a list of differences found.
        """
        diffs: list[str] = []

        if type(kotlin_data) is not type(python_data):
            diffs.append(
                f"{path}: type mismatch - Kotlin={type(kotlin_data).__name__}, Python={type(python_data).__name__}"
            )
            return diffs

        if isinstance(kotlin_data, dict):
            kotlin_keys = set(kotlin_data.keys()) - self.ignore_fields
            python_keys = set(python_data.keys()) - self.ignore_fields

            missing_in_python = kotlin_keys - python_keys
            extra_in_python = python_keys - kotlin_keys

            if missing_in_python:
                diffs.append(f"{path}: missing in Python: {missing_in_python}")
            if extra_in_python:
                diffs.append(f"{path}: extra in Python: {extra_in_python}")

            for key in kotlin_keys & python_keys:
                if key not in self.dynamic_fields:
                    child_diffs = self.compare_structure(
                        kotlin_data[key],
                        python_data[key],
                        f"{path}.{key}" if path else key,
                    )
                    diffs.extend(child_diffs)

        elif isinstance(kotlin_data, list):
            if len(kotlin_data) != len(python_data):
                diffs.append(
                    f"{path}: list length mismatch - Kotlin={len(kotlin_data)}, Python={len(python_data)}"
                )
            else:
                for i, (k_item, p_item) in enumerate(zip(kotlin_data, python_data, strict=False)):
                    child_diffs = self.compare_structure(k_item, p_item, f"{path}[{i}]")
                    diffs.extend(child_diffs)

        return diffs

    def compare_values(
        self,
        kotlin_data: Any,
        python_data: Any,
        path: str = "",
        strict: bool = False,
    ) -> list[str]:
        """
        Compare values in two responses.
        If strict=False, only compares non-dynamic fields.
        """
        diffs: list[str] = []

        if type(kotlin_data) is not type(python_data):
            diffs.append(
                f"{path}: type mismatch - Kotlin={type(kotlin_data).__name__}, Python={type(python_data).__name__}"
            )
            return diffs

        if isinstance(kotlin_data, dict):
            for key in set(kotlin_data.keys()) & set(python_data.keys()):
                if key in self.ignore_fields:
                    continue
                if not strict and key in self.dynamic_fields:
                    continue
                child_diffs = self.compare_values(
                    kotlin_data[key],
                    python_data[key],
                    f"{path}.{key}" if path else key,
                    strict,
                )
                diffs.extend(child_diffs)

        elif isinstance(kotlin_data, list):
            for i, (k_item, p_item) in enumerate(zip(kotlin_data, python_data, strict=False)):
                child_diffs = self.compare_values(k_item, p_item, f"{path}[{i}]", strict)
                diffs.extend(child_diffs)

        else:
            if kotlin_data != python_data:
                diffs.append(
                    f"{path}: value mismatch - Kotlin={kotlin_data!r}, Python={python_data!r}"
                )

        return diffs


@pytest.fixture()
def response_comparator() -> ResponseComparator:
    """Fixture providing a ResponseComparator instance."""
    return ResponseComparator()


class DualEndpointTester:
    """
    Helper for testing both Kotlin and Python endpoints with the same request
    and comparing responses.
    """

    def __init__(
        self,
        kotlin_client: AsyncClient,
        python_client: AsyncClient,
        auth_headers: dict[str, str],
        comparator: ResponseComparator | None = None,
    ):
        self.kotlin = kotlin_client
        self.python = python_client
        self.auth_headers = auth_headers
        self.comparator = comparator or ResponseComparator()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        compare_structure: bool = True,
        compare_values: bool = False,
    ) -> tuple[Any, Any, list[str]]:
        """
        Make GET request to both backends and optionally compare responses.
        Returns (kotlin_response, python_response, differences).
        """
        kotlin_resp = await self.kotlin.get(path, params=params, headers=self.auth_headers)
        python_resp = await self.python.get(path, params=params, headers=self.auth_headers)

        diffs: list[str] = []

        if kotlin_resp.status_code != python_resp.status_code:
            diffs.append(
                f"status_code: Kotlin={kotlin_resp.status_code}, Python={python_resp.status_code}"
            )

        if kotlin_resp.status_code == 200 and python_resp.status_code == 200:
            k_json = kotlin_resp.json()
            p_json = python_resp.json()

            if compare_structure:
                diffs.extend(self.comparator.compare_structure(k_json, p_json))
            if compare_values:
                diffs.extend(self.comparator.compare_values(k_json, p_json))

        return kotlin_resp, python_resp, diffs

    async def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        compare_structure: bool = True,
    ) -> tuple[Any, Any, list[str]]:
        """Make POST request to both backends and compare."""
        kotlin_resp = await self.kotlin.post(path, json=json, headers=self.auth_headers)
        python_resp = await self.python.post(path, json=json, headers=self.auth_headers)

        diffs: list[str] = []

        if kotlin_resp.status_code != python_resp.status_code:
            diffs.append(
                f"status_code: Kotlin={kotlin_resp.status_code}, Python={python_resp.status_code}"
            )

        if kotlin_resp.status_code in (200, 201) and python_resp.status_code in (200, 201):
            if compare_structure:
                diffs.extend(
                    self.comparator.compare_structure(kotlin_resp.json(), python_resp.json())
                )

        return kotlin_resp, python_resp, diffs


@pytest.fixture()
def dual_tester(
    kotlin_client: AsyncClient,
    python_client: AsyncClient,
    auth_headers: dict[str, str],
    response_comparator: ResponseComparator,
) -> DualEndpointTester:
    """Fixture providing a DualEndpointTester for contract comparison tests."""
    return DualEndpointTester(kotlin_client, python_client, auth_headers, response_comparator)


@pytest.fixture(scope="session")
def test_data_factory():
    """Fixture providing a TestDataFactory instance with consistent seed."""
    from tests.contract.fixtures import TestDataFactory

    return TestDataFactory(seed=42)
