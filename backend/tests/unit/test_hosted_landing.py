"""A hosted repository supplies policy through the forge, with no platform files."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from app.domain.review.forgejo_pr import ForgejoClient
from app.domain.review.landing_forge import (
    Check,
    ForgejoLandingReads,
    GitHubLandingReads,
    RequiredCheck,
    gate_run,
)


class Tokens:
    async def installation_token(self):
        return "read-token", None


def test_default_branch_and_all_required_checks_come_from_forge():
    calls = []

    def forge(request):
        if request.url.path == "/graphql":
            assert json.loads(request.content)["variables"] == {
                "owner": "acme",
                "repo": "rocket",
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "defaultBranchRef": {
                                "name": "trunk",
                                "branchProtectionRule": {
                                    "requiredStatusChecks": [
                                        {"context": "build", "app": {"databaseId": 42}}
                                    ]
                                },
                            }
                        }
                    }
                },
            )
        path = request.url.path.removeprefix("/repos/acme/rocket")
        calls.append(path)
        data = {
            "/rules/branches/trunk": [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [
                            {"context": "tests", "integration_id": 43}
                        ]
                    },
                }
            ],
            "/commits/revision/check-runs": {
                "check_runs": [
                    {
                        "name": "build",
                        "app": {"id": 42},
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "tests",
                        "app": {"id": 43},
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ]
            },
            "/commits/revision/status": {"statuses": []},
        }
        assert path in data, path
        return httpx.Response(200, json=data[path])

    async def observe():
        reads = GitHubLandingReads(
            None,
            Tokens(),
            "https://forge.test",
            "acme",
            "rocket",
            transport=httpx.MockTransport(forge),
        )
        await reads.configure()
        gate = gate_run(await reads.checks("revision"), reads.required_checks)
        assert gate.conclusion == "failure"
        assert reads.default_branch == "trunk"
        assert {check.name for check in reads.required_checks} == {"build", "tests"}

    asyncio.run(observe())
    assert not any("main-green" in call or "workflows" in call for call in calls)


def test_all_required_checks_must_finish_and_expected_app_must_report():
    required = (RequiredCheck("build", 42), RequiredCheck("tests", 43))
    build = Check("build", "completed", "success", "", app_id=42)
    unrelated = Check("optional", "completed", "success", "")
    assert gate_run([build, unrelated], required).status != "completed"
    spoof = Check("tests", "completed", "success", "", app_id=99)
    assert gate_run([build, spoof], required).status != "completed"
    tests = Check("tests", "completed", "success", "", app_id=43)
    assert gate_run([build, tests], required).conclusion == "success"


def test_commit_statuses_can_satisfy_a_required_external_ci_context():
    def forge(request):
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": []})
        assert request.url.path.endswith("/status")
        return httpx.Response(
            200,
            json={
                "statuses": [
                    {
                        "context": "external-ci",
                        "state": "success",
                        "target_url": "https://ci.test/7",
                    }
                ]
            },
        )

    async def observe():
        reads = GitHubLandingReads(
            None,
            Tokens(),
            "https://forge.test",
            "acme",
            "rocket",
            transport=httpx.MockTransport(forge),
        )
        gate = gate_run(await reads.checks("revision"), (RequiredCheck("external-ci"),))
        assert gate.conclusion == "success"
        assert gate.url == "https://ci.test/7"

    asyncio.run(observe())


async def test_forgejo_uses_effective_branch_policy_including_context_patterns():
    def forge(request):
        if request.url.path == "/repos/acme/rocket":
            return httpx.Response(200, json={"default_branch": "trunk"})
        assert request.url.path == "/repos/acme/rocket/branches/trunk"
        return httpx.Response(
            200,
            json={
                "enable_status_check": True,
                "status_check_contexts": ["build-*", "test"],
            },
        )

    client = SimpleNamespace(
        client=ForgejoClient("https://forge.test", transport=httpx.MockTransport(forge))
    )
    reads = ForgejoLandingReads(
        client, Tokens(), "https://forge.test", "acme", "rocket"
    )
    await reads.configure()
    checks = [
        Check("build-linux", "completed", "success", ""),
        Check("build-mac", "completed", "failure", ""),
        Check("test", "completed", "success", ""),
    ]
    assert gate_run(checks, reads.required_checks).conclusion == "failure"
