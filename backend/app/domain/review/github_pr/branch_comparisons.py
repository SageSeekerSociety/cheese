"""Bounded, unlocked draft preflight reads; publication still rechecks under claim."""

from collections.abc import Sequence
from typing import Any

import httpx

from app.core import forge_quota
from app.core.forge_http import forge_client
from app.core.sentences import say
from app.domain.review.github_pr._shared import GitHubPrError, _refused, logger
from app.domain.review.github_pr.protocol import (
    BranchComparison,
    BranchComparisonFailure,
    BranchComparisonResult,
    BranchComparisonsDeferred,
    BranchPair,
)

# An engineering bound, not a claim about GitHub's query or point limit.
BRANCH_COMPARISON_BATCH = 50


def _invalid(reason: str) -> GitHubPrError:
    logger.warning("unusable GitHub branch comparison: %s", reason)
    return GitHubPrError(say("githubBranchComparisonInvalid"))


def _query(pairs: Sequence[BranchPair]) -> tuple[str, dict[str, str]]:
    declarations = ["$owner:String!", "$repo:String!"]
    fields = []
    variables = {}
    for index, (branch, base) in enumerate(pairs):
        declarations.extend((f"$branch{index}:String!", f"$base{index}:String!"))
        fields.append(
            f"r{index}:ref(qualifiedName:$branch{index})"
            "{compare(headRef:$base" + str(index) + "){behindBy baseTarget{oid}}}"
        )
        variables[f"branch{index}"] = (
            branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
        )
        variables[f"base{index}"] = base
    return (
        "query("
        + ",".join(declarations)
        + "){repository(owner:$owner,name:$repo){"
        + " ".join(fields)
        + "}}",
        variables,
    )


def _parse(
    payload: Any, pairs: Sequence[BranchPair]
) -> dict[BranchPair, BranchComparisonResult]:
    if not isinstance(payload, dict):
        raise _invalid("response_not_object")
    errors = payload.get("errors", [])
    if not isinstance(errors, list) or any(
        not isinstance(error, dict) for error in errors
    ):
        raise _invalid("invalid_errors")
    if any(error.get("type") == "RATE_LIMITED" for error in errors):
        raise BranchComparisonsDeferred(
            say("forgeQuotaExhausted", wait=say("forgeQuotaBackSoon"))
        )
    failures: dict[str, list[str]] = {}
    aliases = {f"r{index}" for index in range(len(pairs))}
    for error in errors:
        path = error.get("path")
        if (
            not isinstance(path, list)
            or len(path) < 2
            or path[0] != "repository"
            or not isinstance(path[1], str)
            or path[1] not in aliases
        ):
            raise _invalid(str(error.get("message") or "unscoped_query_error")[:300])
        failures.setdefault(path[1], []).append(
            str(error.get("message") or "query failed")[:300]
        )
    data = payload.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    if not isinstance(repository, dict):
        raise _invalid("missing_repository")
    results: dict[BranchPair, BranchComparisonResult] = {}
    for index, pair in enumerate(pairs):
        alias = f"r{index}"
        if alias in failures:
            results[pair] = BranchComparisonFailure("; ".join(failures[alias])[:300])
            continue
        if alias not in repository:
            results[pair] = BranchComparisonFailure("Missing branch response alias")
            continue
        branch = repository[alias]
        if branch is None:
            results[pair] = None
            continue
        comparison = branch.get("compare") if isinstance(branch, dict) else None
        if not isinstance(comparison, dict):
            results[pair] = BranchComparisonFailure("Missing branch comparison")
            continue
        target = comparison.get("baseTarget")
        head = target.get("oid") if isinstance(target, dict) else None
        ahead = comparison.get("behindBy")
        if not isinstance(head, str) or not head or type(ahead) is not int or ahead < 0:
            results[pair] = BranchComparisonFailure("Invalid comparison head or count")
            continue
        # The task ref is GraphQL's base. Its commits absent from the desired
        # base (GraphQL's head) are behindBy, not aheadBy.
        results[pair] = BranchComparison(head, ahead)
    return results


async def read_branch_comparisons(
    *,
    api_base: str,
    transport: httpx.AsyncBaseTransport | None,
    headers: dict[str, str],
    owner: str,
    repo: str,
    pairs: Sequence[BranchPair],
) -> dict[BranchPair, BranchComparisonResult]:
    unique = list(dict.fromkeys(pairs))
    if not unique:
        return {}
    results: dict[BranchPair, BranchComparisonResult] = {}
    url = f"{api_base}/graphql"
    async with forge_client(transport=transport, timeout=30.0) as client:
        for offset in range(0, len(unique), BRANCH_COMPARISON_BATCH):
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
                raise BranchComparisonsDeferred(
                    say("forgeQuotaExhausted", wait=say("forgeQuotaBackSoon"))
                )
            batch = unique[offset : offset + BRANCH_COMPARISON_BATCH]
            query, variables = _query(batch)
            variables.update(owner=owner, repo=repo)
            response = await client.post(
                url, headers=headers, json={"query": query, "variables": variables}
            )
            if response.status_code != 200:
                error = _refused("githubRefusedCommitOrder", response)
                if response.status_code == 429 or forge_quota.rate_limited(response):
                    if installation is not None:
                        forge_quota.refuse(
                            installation, forge_quota.wait_seconds(response)
                        )
                    raise BranchComparisonsDeferred(str(error))
                raise error
            try:
                payload = response.json()
            except ValueError as exc:
                raise _invalid("response_not_json") from exc
            try:
                results.update(_parse(payload, batch))
            except BranchComparisonsDeferred:
                if installation is not None:
                    forge_quota.refuse(installation, forge_quota.wait_seconds(response))
                raise
    return results
