#!/usr/bin/env python3
"""Check GitHub API health and the current project's installation access."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Response:
    status: int
    headers: Any
    body: bytes


def request(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return Response(response.status, response.headers, response.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, error.headers, error.read())


def parse_json(response: Response) -> dict[str, Any]:
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("response was not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("response was not a JSON object")
    return value


def rate_limited(response: Response) -> bool:
    remaining = response.headers.get("X-RateLimit-Remaining")
    return response.status == 429 or (
        response.status == 403
        and (remaining == "0" or response.headers.get("Retry-After") is not None)
    )


def graphql_rate_limited(payload: dict[str, Any], response: Response) -> bool:
    if rate_limited(response) or response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(error, dict)
        and (
            "rate limit" in str(error.get("message", "")).casefold()
            or error.get("type") == "RATE_LIMITED"
        )
        for error in errors
    )


def github_api_check(api_url: str, graphql_url: str, token: str) -> tuple[bool, str]:
    try:
        rest = request(f"{api_url.rstrip('/')}/rate_limit", token=token)
        if rest.status == 503:
            return (
                False,
                "GitHub REST API returned 503; retry after the service recovers.",
            )
        if rate_limited(rest):
            return (
                False,
                "GitHub REST API rate limit is exhausted; wait for the reset time.",
            )
        if rest.status != 200:
            return (
                False,
                f"GitHub REST API returned HTTP {rest.status}; check network and token validity.",
            )
        resources = parse_json(rest).get("resources")
        rate = resources.get("core") if isinstance(resources, dict) else None
        if not isinstance(rate, dict) or not isinstance(rate.get("remaining"), int):
            return (
                False,
                "GitHub REST API response omitted the core rate limit; check the API response.",
            )

        query = json.dumps(
            {"query": "query { viewer { login } rateLimit { remaining resetAt } }"}
        ).encode()
        graph = request(graphql_url, token=token, method="POST", body=query)
        if graph.status == 503:
            return (
                False,
                "GitHub GraphQL API returned 503; retry after the service recovers.",
            )
        if rate_limited(graph):
            return (
                False,
                "GitHub GraphQL API rate limit is exhausted; wait for the reset time.",
            )
        if graph.status != 200:
            return (
                False,
                f"GitHub GraphQL API returned HTTP {graph.status}; check network and token validity.",
            )
        payload = parse_json(graph)
        if graphql_rate_limited(payload, graph):
            return (
                False,
                "GitHub GraphQL API rate limit is exhausted; wait for the reset time.",
            )
        result = payload.get("data", {})
        viewer = result.get("viewer") if isinstance(result, dict) else None
        gql_rate = result.get("rateLimit") if isinstance(result, dict) else None
        if not isinstance(viewer, dict) or not viewer.get("login"):
            return (
                False,
                "GitHub GraphQL API did not identify the authenticated viewer; check token access.",
            )
        if payload.get("errors"):
            return (
                False,
                "GitHub GraphQL API returned errors; check token access and API availability.",
            )
        if not isinstance(gql_rate, dict) or not isinstance(
            gql_rate.get("remaining"), int
        ):
            return (
                False,
                "GitHub GraphQL API response omitted the rate limit; check the API response.",
            )
        return (
            True,
            f"REST core remaining={rate['remaining']}; GraphQL remaining={gql_rate['remaining']}.",
        )
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as error:
        return (
            False,
            f"GitHub API connection or response failed ({type(error).__name__}); check network access.",
        )


def parse_token_permissions(value: Any) -> dict[str, str] | None:
    if not isinstance(value, str) or not value:
        return None
    grants: dict[str, str] = {}
    for entry in value.split(","):
        name, separator, access = entry.partition(":")
        name = name.strip()
        access = access.strip()
        if not separator or not name or not access or name in grants:
            return None
        grants[name] = access
    return grants


def installation_check(
    api_url: str, repo: str, token: str, token_permissions: Any
) -> tuple[bool, str]:
    parts = repo.split("/", 1)
    if len(parts) != 2 or not all(parts):
        return (
            False,
            "The installation token did not provide a valid owner/repository; check project setup.",
        )
    encoded_repo = "/".join(urllib.parse.quote(part, safe="") for part in parts)
    try:
        response = request(f"{api_url.rstrip('/')}/repos/{encoded_repo}", token=token)
        if response.status == 503:
            return (
                False,
                "GitHub returned 503 while checking the repository; retry after the service recovers.",
            )
        if rate_limited(response):
            return (
                False,
                "GitHub rate limit is exhausted during repository access check; wait for reset.",
            )
        if response.status in (401, 403, 404):
            return (
                False,
                f"Installation token cannot read the target repository (HTTP {response.status}); restore the GitHub App installation or repository access.",
            )
        if response.status != 200:
            return (
                False,
                f"Repository permission check returned HTTP {response.status}; inspect GitHub access.",
            )
        payload = parse_json(response)
        full_name = payload.get("full_name")
        if not isinstance(full_name, str) or full_name.casefold() != repo.casefold():
            return (
                False,
                "GitHub returned a different repository; check the project's GitHub installation.",
            )
        grants = parse_token_permissions(token_permissions)
        if grants is None:
            return (
                False,
                "GitHub App token permissions are missing or malformed; reconnect the installation.",
            )
        if grants.get("metadata") not in {"read", "write"}:
            return (
                False,
                "GitHub App token lacks metadata read access; grant repository read permission.",
            )
        if grants.get("contents") != "write":
            return (
                False,
                "GitHub App token lacks contents:write; grant repository write permission.",
            )
        return (
            True,
            "Installation token can read and push to the configured repository.",
        )
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as error:
        return (
            False,
            f"Repository access check failed ({type(error).__name__}); check token and network access.",
        )


def get_installation() -> tuple[str, str, str, str]:
    cheese_api = os.environ.get("CHEESE_API")
    cheese_token = os.environ.get("CHEESE_TOKEN")
    if not cheese_api or not cheese_token:
        raise ValueError(
            "CHEESE_API or CHEESE_TOKEN is missing; run this inside an authenticated Cheese session."
        )
    response = request(
        f"{cheese_api.rstrip('/')}/sandbox/forge-token",
        extra_headers={"Authorization": f"Bearer {cheese_token}"},
    )
    if response.status != 200:
        raise ValueError(
            f"Could not obtain the GitHub App installation token (HTTP {response.status}); refresh the Cheese session."
        )
    payload = parse_json(response)
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("kind") != "github_app":
        raise ValueError(
            "The current project does not provide a GitHub App installation token; check the configured forge."
        )
    token = data.get("token")
    repo = data.get("repo")
    api_url = data.get("api_url") or "https://api.github.com"
    if not isinstance(api_url, str):
        raise ValueError(
            "The installation-token response contained an invalid REST API URL."
        )
    graphql_url = data.get("graphql_url") or f"{api_url.rstrip('/')}/graphql"
    if not isinstance(graphql_url, str):
        raise ValueError(
            "The installation-token response contained an invalid GraphQL API URL."
        )
    if not isinstance(token, str) or not token or not isinstance(repo, str) or not repo:
        raise ValueError(
            "The installation-token response omitted token or repository metadata; reconnect the GitHub App."
        )
    expected_repo = os.environ.get("GITHUB_REPOSITORY")
    if expected_repo and expected_repo.casefold() != repo.casefold():
        raise ValueError(
            "The installation token's repository does not match GITHUB_REPOSITORY; check project setup."
        )
    return token, repo, api_url, graphql_url, data.get("permissions")


def main() -> int:
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "github"):
        print(f"Usage: {sys.argv[0]} [github]", file=sys.stderr)
        return 2
    try:
        token, repo, api_url, graphql_url, token_permissions = get_installation()
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"[1] GitHub API: FAIL — installation token unavailable ({error})")
        print("[2] Repository access: NOT RUN — resolve the token failure above.")
        return 1

    first_ok, first_message = github_api_check(api_url, graphql_url, token)
    print(f"[1] GitHub API: {'OK' if first_ok else 'FAIL'} — {first_message}")
    if not first_ok:
        print("[2] Repository access: NOT RUN — resolve the GitHub API failure above.")
        return 1

    second_ok, second_message = installation_check(
        api_url, repo, token, token_permissions
    )
    print(f"[2] Repository access: {'OK' if second_ok else 'FAIL'} — {second_message}")
    return 0 if second_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
