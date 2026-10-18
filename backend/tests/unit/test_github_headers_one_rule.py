"""GitHub REST 请求头在全仓只有一处拼装规则。

``github_app``、``oauth/services/providers`` 和 ``ratchet/artifacts`` 三个模块都向
``api.github.com`` 发请求：前两者只带 ``Authorization`` 与
``Accept: application/vnd.github+json``，``ratchet/artifacts`` 额外带
``X-GitHub-Api-Version: 2022-11-28``。三处統一从 ``app.core.github_headers``
取 ``github_headers``，由 ``api_version`` 显式控制版本头，既防止日後改请求头漏改一处，
也保证各调用点实际发出的请求头与改前逐字节一致。
"""

import ast
import inspect

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import github_headers as github_headers_module
from app.core.github_headers import (
    GITHUB_ACCEPT,
    GITHUB_API_VERSION,
    github_headers,
)
from app.domain.oauth.services import providers
from app.domain.oauth.services.providers import GitHubProvider, OAuthProviderConfig
from app.domain.project import github_app
from app.domain.project.github_app import GitHubAppTokens
from app.domain.ratchet import artifacts
from app.domain.ratchet.artifacts import GitHubArtifacts


def _call_count_to_github_headers(module: object) -> int:
    tree = ast.parse(inspect.getsource(module))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "github_headers"
    )


def _string_literals(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_all_three_modules_point_to_the_one_github_headers_function():
    assert github_app.github_headers is github_headers
    assert providers.github_headers is github_headers
    assert artifacts.github_headers is github_headers
    assert github_headers_module.github_headers is github_headers

    assert _call_count_to_github_headers(github_app) == 5
    assert _call_count_to_github_headers(providers) == 2
    assert _call_count_to_github_headers(artifacts) == 1

    for module in (github_app, providers, artifacts):
        literals = _string_literals(module)
        assert GITHUB_ACCEPT not in literals
        assert GITHUB_API_VERSION not in literals
        assert "X-GitHub-Api-Version" not in literals


def test_accept_is_consistent_and_version_header_is_opt_in():
    plain = github_headers("tok-abc")
    versioned = github_headers("tok-abc", api_version=True)

    assert plain == {
        "Authorization": "Bearer tok-abc",
        "Accept": "application/vnd.github+json",
    }
    assert versioned == {
        "Authorization": "Bearer tok-abc",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    assert plain["Accept"] == versioned["Accept"] == GITHUB_ACCEPT
    assert "X-GitHub-Api-Version" not in plain
    assert versioned["X-GitHub-Api-Version"] == GITHUB_API_VERSION


def _github_header_wire_bytes(request: httpx.Request) -> list[tuple[bytes, bytes]]:
    tracked = {b"authorization", b"accept", b"x-github-api-version"}
    return [(k, v) for k, v in request.headers.raw if k.lower() in tracked]


@pytest.mark.anyio
async def test_emitted_headers_match_pre_change_bytes_across_all_three_modules(
    tmp_path, monkeypatch
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_path = tmp_path / "app.pem"
    pem_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    monkeypatch.setattr(github_app, "_app_jwt", lambda self: "jwt-fixed", raising=False)
    monkeypatch.setattr(GitHubAppTokens, "_app_jwt", lambda self: "jwt-fixed")

    seen: list[tuple[str, list[tuple[bytes, bytes]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((f"{request.method} {path}", _github_header_wire_bytes(request)))
        if path == "/app/installations/7":
            return httpx.Response(200, json={"permissions": {"contents": "read"}})
        if path == "/app/installations/7/access_tokens":
            return httpx.Response(
                201,
                json={"token": "ghs_minted", "expires_at": "2099-01-01T00:00:00Z"},
            )
        if path == "/rate_limit":
            return httpx.Response(
                200,
                json={"resources": {"core": {"remaining": 4999, "limit": 5000}}},
            )
        if path == "/repos/acme/demo":
            return httpx.Response(200, json={"id": 42, "full_name": "acme/demo"})
        if path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 7}]})
        if path == "/user":
            return httpx.Response(
                200, json={"id": 99, "login": "octocat", "name": "Octocat"}
            )
        if path == "/user/emails":
            return httpx.Response(
                200,
                json=[{"email": "octo@example.com", "primary": True, "verified": True}],
            )
        if path == "/repos/acme/demo/actions/artifacts":
            return httpx.Response(200, json={"artifacts": []})
        if path == "/repos/acme/demo/actions/artifacts/11/zip":
            return httpx.Response(200, content=b"zip-bytes")
        raise AssertionError(f"Unexpected path: {path}")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(**(kwargs | {"transport": transport})),
    )

    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=str(pem_path),
        installation_id=7,
        transport=transport,
    )
    await minter.core_quota()
    await github_app._describe(minter, "/repos/acme/demo")
    await github_app.list_user_installations("user-tok")

    oauth_provider = GitHubProvider(
        OAuthProviderConfig(
            id="github",
            name="GitHub",
            client_id="cid",
            client_secret="csec",
            authorization_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            redirect_url="https://example.com/callback",
            scope=["read:user"],
        )
    )
    await oauth_provider.get_user_info("oauth-tok")

    reader = GitHubArtifacts(transport=transport)
    await reader.list(owner="acme", repo="demo", token="ratchet-tok", limit=1)
    await reader.download(
        owner="acme", repo="demo", artifact_id=11, token="ratchet-tok"
    )

    expected_jwt = [
        (b"Authorization", b"Bearer jwt-fixed"),
        (b"Accept", b"application/vnd.github+json"),
    ]
    expected_minted = [
        (b"Authorization", b"Bearer ghs_minted"),
        (b"Accept", b"application/vnd.github+json"),
    ]
    expected_user = [
        (b"Authorization", b"Bearer user-tok"),
        (b"Accept", b"application/vnd.github+json"),
    ]
    expected_oauth = [
        (b"Authorization", b"Bearer oauth-tok"),
        (b"Accept", b"application/vnd.github+json"),
    ]
    expected_ratchet = [
        (b"Authorization", b"Bearer ratchet-tok"),
        (b"Accept", b"application/vnd.github+json"),
        (b"X-GitHub-Api-Version", b"2022-11-28"),
    ]

    assert seen == [
        ("GET /app/installations/7", expected_jwt),
        ("POST /app/installations/7/access_tokens", expected_jwt),
        ("GET /rate_limit", expected_minted),
        ("GET /repos/acme/demo", expected_minted),
        ("GET /user/installations", expected_user),
        ("GET /user", expected_oauth),
        ("GET /user/emails", expected_oauth),
        ("GET /repos/acme/demo/actions/artifacts", expected_ratchet),
        ("GET /repos/acme/demo/actions/artifacts/11/zip", expected_ratchet),
    ]
