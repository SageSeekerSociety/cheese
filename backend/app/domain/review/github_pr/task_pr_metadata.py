"""A bounded task-poll read, not a PR verdict or permission to merge."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core import forge_quota
from app.core.forge_http import forge_client
from app.core.sentences import say
from app.domain.review.github_pr._shared import GitHubPrError, _refused, logger

TASK_PR_METADATA_BATCH = 10


@dataclass(frozen=True)
class TaskPrMetadata:
    number: int
    head_sha: str
    state: Literal["open", "closed"]
    merged: bool


@dataclass(frozen=True)
class TaskPrMetadataFailure:
    reason: str


type TaskPrMetadataResult = TaskPrMetadata | TaskPrMetadataFailure


class TaskPrMetadataDeferred(GitHubPrError):
    """The remaining metadata queries must wait, not fall back to REST."""


def _unusable(reason: str) -> GitHubPrError:
    logger.warning("unusable GitHub task PR metadata: %s", reason)
    return GitHubPrError(say("upstreamUnavailable"))


def _query(numbers: Sequence[int]) -> tuple[str, dict[str, int]]:
    declarations = ["$owner:String!", "$repo:String!"]
    fields = []
    variables = {}
    for index, number in enumerate(numbers):
        declarations.append(f"$n{index}:Int!")
        variables[f"n{index}"] = number
        fields.append(
            f"p{index}:pullRequest(number:$n{index}){{number state merged headRefOid}}"
        )
    return (
        "query("
        + ",".join(declarations)
        + "){repository(owner:$owner,name:$repo){"
        + " ".join(fields)
        + "}}",
        variables,
    )


def _parse(payload: Any, numbers: Sequence[int]) -> dict[int, TaskPrMetadataResult]:
    if not isinstance(payload, dict):
        raise _unusable("response_not_object")
    errors = payload.get("errors", [])
    if not isinstance(errors, list) or any(not isinstance(e, dict) for e in errors):
        raise _unusable("invalid_errors")
    if any(e.get("type") == "RATE_LIMITED" for e in errors):
        raise TaskPrMetadataDeferred(
            say("forgeQuotaExhausted", wait=say("forgeQuotaBackSoon"))
        )
    failed = set()
    aliases = {f"p{i}" for i in range(len(numbers))}
    for error in errors:
        path = error.get("path")
        if (
            not isinstance(path, list)
            or len(path) < 2
            or path[0] != "repository"
            or not isinstance(path[1], str)
            or path[1] not in aliases
        ):
            raise _unusable("unscoped_query_error")
        failed.add(path[1])
    data = payload.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    if not isinstance(repository, dict):
        raise _unusable("missing_repository")
    results: dict[int, TaskPrMetadataResult] = {}
    for index, number in enumerate(numbers):
        alias = f"p{index}"
        value = repository.get(alias)
        if alias in failed or not isinstance(value, dict):
            results[number] = TaskPrMetadataFailure("unreadable_pr")
            continue
        head, state, merged = (
            value.get("headRefOid"),
            value.get("state"),
            value.get("merged"),
        )
        if (
            type(value.get("number")) is not int
            or value["number"] != number
            or not isinstance(head, str)
            or not head
            or state not in ("OPEN", "CLOSED", "MERGED")
            or type(merged) is not bool
            or merged != (state == "MERGED")
        ):
            results[number] = TaskPrMetadataFailure("invalid_pr_metadata")
            continue
        results[number] = TaskPrMetadata(
            number=number,
            head_sha=head,
            state="open" if state == "OPEN" else "closed",
            merged=merged,
        )
    return results


async def read_task_pr_metadata(
    *,
    api_base: str,
    transport: httpx.AsyncBaseTransport | None,
    headers: dict[str, str],
    owner: str,
    repo: str,
    numbers: Sequence[int],
) -> dict[int, TaskPrMetadataResult]:
    unique = list(dict.fromkeys(numbers))
    if not unique:
        return {}
    results: dict[int, TaskPrMetadataResult] = {}
    url = f"{api_base}/graphql"
    async with forge_client(transport=transport, timeout=20.0) as client:
        for offset in range(0, len(unique), TASK_PR_METADATA_BATCH):
            request = httpx.Request("POST", url, headers=headers)
            installation = forge_quota.installation_of(request)
            reading = (
                forge_quota.reading(installation) if installation is not None else None
            )
            if installation is not None and (
                forge_quota.refused_until(installation) is not None
                or (
                    reading is not None
                    and reading.limit > 0
                    and reading.remaining
                    < reading.limit * forge_quota.BACKGROUND_STOP_QUOTA
                )
            ):
                raise TaskPrMetadataDeferred(
                    say("forgeQuotaExhausted", wait=say("forgeQuotaBackSoon"))
                )
            batch = unique[offset : offset + TASK_PR_METADATA_BATCH]
            query, variables = _query(batch)
            response = await client.post(
                url,
                headers=headers,
                json={
                    "query": query,
                    "variables": {**variables, "owner": owner, "repo": repo},
                },
            )
            if response.status_code != 200:
                error = _refused("githubRefusedPrStatus", response)
                if response.status_code == 429 or forge_quota.rate_limited(response):
                    raise TaskPrMetadataDeferred(str(error))
                raise error
            try:
                payload = response.json()
            except ValueError as exc:
                raise _unusable("response_not_json") from exc
            results.update(_parse(payload, batch))
    return results
