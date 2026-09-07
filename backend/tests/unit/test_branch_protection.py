"""Branch-protection policy reads, writes and the GitHub snapshot (#718).

The read side must never raise — it sits on the accept path, where one bad
settings write must not 500 every merge. The GitHub snapshot must never raise
either, and 403 is its everyday answer, not an error: a free-plan private repo
403s every protection endpoint (the fact issue #718 is built on).
"""

import json
from typing import Any, cast

import httpx
import pytest

from app.domain.project.protection import (
    BranchProtection,
    apply_branch_protection_update,
    branch_protection_of,
    github_repo_snapshot,
)


def _project(settings: Any):
    """Only ``settings`` is read; a stand-in avoids a mapped instance."""

    class _FakeProject:
        def __init__(self) -> None:
            self.settings = settings

    return cast(Any, _FakeProject())


# --- Read side ------------------------------------------------------------


def test_defaults_when_nothing_is_configured():
    assert branch_protection_of(None) == BranchProtection()
    bp = branch_protection_of(_project(None))
    assert bp.required_checks == ()
    assert bp.strict is False
    assert bp.dismiss_stale is True  # GitHub 默认相反：这里推代码的是芝士
    assert bp.auto_merge_allowed is False
    assert bp.override_handles is None  # = owner + leads
    assert bp.approvals_required == 1
    assert bp.default_reviewer == ""


def test_reads_a_configured_policy():
    bp = branch_protection_of(
        _project(
            {
                "approvals_required": 2,
                "branch_protection": {
                    "required_checks": [{"name": "test", "paths": ["backend/**"]}],
                    "strict": True,
                    "dismiss_stale": False,
                    "auto_merge_allowed": True,
                    "override_handles": ["alice"],
                    "default_reviewer": "bob",
                },
            }
        )
    )
    assert [(c.name, c.paths) for c in bp.required_checks] == [
        ("test", ("backend/**",))
    ]
    assert bp.strict is True
    assert bp.dismiss_stale is False
    assert bp.auto_merge_allowed is True
    assert bp.override_handles == ("alice",)
    assert bp.approvals_required == 2
    assert bp.default_reviewer == "bob"


def test_a_malformed_stored_value_never_raises():
    """Field-by-field fallback: one bad key must not take the others down."""
    bp = branch_protection_of(
        _project(
            {
                "approvals_required": "many",
                "branch_protection": {
                    "required_checks": [
                        "not-a-dict",
                        {"name": ""},
                        {"name": "ok", "paths": "not-a-list"},
                    ],
                    "strict": 1,
                    "override_handles": "everyone",
                    "default_reviewer": {"x": 1},
                },
            }
        )
    )
    assert [(c.name, c.paths) for c in bp.required_checks] == [("ok", ())]
    assert bp.strict is True  # truthy 1, coerced not crashed
    assert bp.override_handles is None  # wrong shape = unconfigured
    assert bp.approvals_required == 1

    assert branch_protection_of(_project({"branch_protection": "zzz"})) == (
        BranchProtection()
    )


# --- Write side -----------------------------------------------------------


def test_partial_update_only_touches_present_keys():
    stored = {"strict": True, "default_reviewer": "bob"}
    new = apply_branch_protection_update(stored, {"dismiss_stale": False})
    assert new == {"strict": True, "default_reviewer": "bob", "dismiss_stale": False}
    assert stored == {"strict": True, "default_reviewer": "bob"}  # not mutated


def test_update_normalizes_and_clears():
    new = apply_branch_protection_update(
        {"override_handles": ["alice"], "default_reviewer": "bob"},
        {
            "required_checks": [{"name": "  test ", "paths": ["backend/**"]}],
            "override_handles": None,
            "default_reviewer": "",
        },
    )
    assert new == {"required_checks": [{"name": "test", "paths": ["backend/**"]}]}

    # Duplicate handles collapse; an omitted paths key stays omitted.
    new = apply_branch_protection_update(
        {}, {"override_handles": ["a", "a", "b"], "required_checks": [{"name": "t"}]}
    )
    assert new["override_handles"] == ["a", "b"]
    assert new["required_checks"] == [{"name": "t"}]


@pytest.mark.parametrize(
    "body",
    [
        {"required_checks": "test"},
        {"required_checks": [{"name": ""}]},
        {"required_checks": [{"name": "x" * 300}]},
        {"required_checks": [{"name": "t", "paths": ["  "]}]},
        {"required_checks": [{"name": "t", "paths": ["/abs/**"]}]},
        {"required_checks": [{"name": "t", "paths": "backend/**"}]},
        {"strict": "yes"},
        {"dismiss_stale": 1},
        {"override_handles": "everyone"},
        {"override_handles": [""]},
        {"default_reviewer": "x" * 100},
    ],
)
def test_update_rejects_bad_input(body):
    with pytest.raises(ValueError):
        apply_branch_protection_update({}, body)


# --- The GitHub snapshot: degrade, never raise ----------------------------


def _transport(routes: dict[str, tuple[int, object]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, (status, body) in routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(status, json=body)
        raise AssertionError(f"unexpected call: {request.url.path}")

    return httpx.MockTransport(handler)


_REPO = {
    "default_branch": "main",
    "allow_squash_merge": True,
    "allow_merge_commit": True,
    "allow_rebase_merge": False,
}


async def test_free_plan_403s_degrade_to_unknown_not_500():
    """The founding fact of #718: this very repo answers 403 to every
    protection endpoint. That is an everyday answer — enforced=False."""
    merge_method, gh = await github_repo_snapshot(
        "acme/widgets",
        "tok",
        transport=_transport(
            {
                "/repos/acme/widgets": (200, _REPO),
                "/branches/main/protection": (403, {"message": "Upgrade"}),
                "/rules/branches/main": (403, {"message": "Upgrade"}),
            }
        ),
    )
    assert merge_method == "squash"
    assert gh.enforced is False
    assert gh.status == "unknown"


async def test_no_protection_anywhere_is_a_definitive_none():
    merge_method, gh = await github_repo_snapshot(
        "acme/widgets",
        "tok",
        transport=_transport(
            {
                "/repos/acme/widgets": (200, _REPO),
                "/branches/main/protection": (404, {"message": "Not protected"}),
                "/rules/branches/main": (200, []),
            }
        ),
    )
    assert (merge_method, gh.status, gh.enforced) == ("squash", "none", False)


async def test_branch_protection_on_github_reads_as_enforced():
    _, gh = await github_repo_snapshot(
        "acme/widgets",
        "tok",
        transport=_transport(
            {
                "/repos/acme/widgets": (200, _REPO),
                "/branches/main/protection": (200, {"required_status_checks": {}}),
            }
        ),
    )
    assert gh.enforced is True
    assert gh.status == "enforced"


async def test_active_rulesets_read_as_enforced_too():
    _, gh = await github_repo_snapshot(
        "acme/widgets",
        "tok",
        transport=_transport(
            {
                "/repos/acme/widgets": (200, _REPO),
                "/branches/main/protection": (404, {}),
                "/rules/branches/main": (200, [{"type": "pull_request"}]),
            }
        ),
    )
    assert gh.enforced is True


async def test_merge_method_follows_repo_settings():
    repo = dict(_REPO, allow_squash_merge=False)
    merge_method, _ = await github_repo_snapshot(
        "acme/widgets",
        "tok",
        transport=_transport(
            {
                "/repos/acme/widgets": (200, repo),
                "/branches/main/protection": (404, {}),
                "/rules/branches/main": (200, []),
            }
        ),
    )
    assert merge_method == "merge"


async def test_unreadable_repo_missing_token_and_network_failure_all_degrade():
    merge_method, gh = await github_repo_snapshot(
        "acme/widgets",
        "tok",
        transport=_transport({"/repos/acme/widgets": (404, {})}),
    )
    assert (merge_method, gh.status) == ("squash", "unknown")

    merge_method, gh = await github_repo_snapshot("acme/widgets", None)
    assert (merge_method, gh.status) == ("squash", "unknown")

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to github")

    merge_method, gh = await github_repo_snapshot(
        "acme/widgets", "tok", transport=httpx.MockTransport(_boom)
    )
    assert (merge_method, gh.status) == ("squash", "unknown")

    def _garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    merge_method, gh = await github_repo_snapshot(
        "acme/widgets", "tok", transport=httpx.MockTransport(_garbage)
    )
    assert (merge_method, gh.status) == ("squash", "unknown")
    assert isinstance(json.dumps(gh.detail), str)  # detail is always a string
