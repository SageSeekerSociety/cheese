"""Read task PR facts through the actual client, with real task transactions."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import event

from app.domain.review import forge_clients, pr_poll
from app.domain.review.github_pr.legacy import GitHubPRClient
from app.domain.room_task.models import Task
from tests.integration.test_accept_pr import _chat_service, _make_project, _make_topic
from tests.integration.test_accept_pr import app_world as _app_world_fixture

app_world = pytest.fixture(_app_world_fixture.__wrapped__)


def seed_prs(client, count=10, *, running=False):
    project = uuid.UUID(_make_project(client))
    room = uuid.UUID(_make_topic(client, str(project)))

    async def seed():
        async with client.test_factory() as session:
            tasks = [
                Task(
                    project_id=project,
                    room_id=room,
                    title=f"PR metadata consumer {i}",
                    pr_number=100 + i,
                    pr_url=f"https://github.com/owner/repo/pull/{100 + i}",
                    pr_checks_head=f"{100 + i:040x}" if running else None,
                    pr_checks_state="running" if running else None,
                )
                for i in range(count)
            ]
            session.add_all(tasks)
            await session.commit()
            return [task.id for task in tasks]

    return project, client.portal.call(seed)


def use_wire_client(monkeypatch, handler):
    def make(owner, repo, tokens, **kwargs):
        return GitHubPRClient(
            owner, repo, tokens, transport=httpx.MockTransport(handler), **kwargs
        )

    monkeypatch.setattr(forge_clients, "GitHubPRClient", make)


def wire_metadata(request, *, opened=False, bad_first=False):
    body = json.loads(request.content)
    aliases = {
        "p" + key[1:]: {
            "number": number,
            "state": "OPEN" if opened else "CLOSED",
            "merged": False,
            "headRefOid": f"{number:040x}",
        }
        for key, number in body["variables"].items()
        if key.startswith("n")
    }
    if bad_first:
        aliases["p0"] = None
    return httpx.Response(200, json={"data": {"repository": aliases}})


@pytest.fixture
def idle_metadata_connections(client):
    active = set()
    engines = {
        factory.kw["bind"].sync_engine
        for factory in (client.test_factory, client.test_request_factory)
    }

    def checkout(_connection, record, _proxy):
        active.add(record)

    def checkin(_connection, record):
        active.discard(record)

    for engine in engines:
        event.listen(engine, "checkout", checkout)
        event.listen(engine, "checkin", checkin)
    yield active
    for engine in engines:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)


def poll(client, project, *, said=None):
    async def record(_session, _event, *, task_id, content):
        assert said is not None, "this PR has no transition to announce"
        said.append((task_id, content))

    async def announce(_session, **kwargs):
        assert said is not None, "this PR has no transition to announce"

    async def teammate(*args, **kwargs):
        raise AssertionError("an unmerged PR must not settle a task")

    def run():
        return pr_poll.poll_uncarded_task_prs(
            _chat_service(),
            announce,
            project,
            record_instruction=record,
            task_teammate=teammate,
        )

    return client.portal.call(run)


@pytest.mark.parametrize("opened", [False, True], ids=["closed", "open-with-checks"])
def test_ten_task_prs_share_metadata_but_keep_the_existing_checks_reads(
    client, app_world, monkeypatch, opened, idle_metadata_connections
):
    project, _ids = seed_prs(client)
    sent = []

    def handler(request):
        sent.append(request)
        if request.method == "POST" and request.url.path == "/graphql":
            assert not idle_metadata_connections
            return wire_metadata(request, opened=opened)
        if request.url.path.endswith("/check-runs"):
            assert request.url.params["per_page"] == "50"
            return httpx.Response(200, json={"check_runs": []})
        if "/pulls/" in request.url.path:
            number = int(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(
                200,
                json={
                    "number": number,
                    "state": "open" if opened else "closed",
                    "merged": False,
                    "head": {"sha": f"{number:040x}"},
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    use_wire_client(monkeypatch, handler)
    outcome = poll(client, project)
    assert outcome == {"tasks_checked": 10, "tasks_merged": 0, "errors": []}
    metadata = [r for r in sent if r.method == "POST" and r.url.path == "/graphql"]
    checks = [r for r in sent if r.url.path.endswith("/check-runs")]
    individual = [r for r in sent if "/pulls/" in r.url.path]
    assert len(metadata) == 1
    assert individual == []
    assert len(checks) == (10 if opened else 0)
    assert len(sent) == (11 if opened else 1)


@pytest.mark.parametrize("changed", ["number", "project"])
def test_metadata_cannot_wake_a_task_that_changed_its_pr_identity(
    client, app_world, monkeypatch, changed
):
    project, ids = seed_prs(client, 1, running=True)
    other_project = uuid.UUID(_make_project(client))
    said = []
    sent = []

    async def handler(request):
        sent.append(request)
        if request.url.path == "/graphql" or "/pulls/" in request.url.path:
            async with client.test_factory() as session:
                row = await session.get(Task, ids[0])
                if changed == "number":
                    row.pr_number = 999
                else:
                    row.project_id = other_project
                await session.commit()
            if request.url.path == "/graphql":
                return wire_metadata(request, opened=True)
            return httpx.Response(
                200,
                json={"state": "open", "merged": False, "head": {"sha": f"{100:040x}"}},
            )
        return httpx.Response(
            200,
            json={
                "check_runs": [
                    {"name": "CI", "status": "completed", "conclusion": "success"}
                ]
            },
        )

    use_wire_client(monkeypatch, handler)
    outcome = poll(client, project, said=said)
    assert outcome["errors"] == [] and outcome["tasks_merged"] == 0
    assert said == []
    assert not any(r.url.path.endswith("/check-runs") for r in sent)


def test_an_unreadable_metadata_item_does_not_hide_valid_siblings(
    client, app_world, monkeypatch
):
    project, _ids = seed_prs(client)
    sent = []

    def handler(request):
        sent.append(request)
        assert request.method == "POST" and request.url.path == "/graphql"
        return wire_metadata(request, bad_first=True)

    use_wire_client(monkeypatch, handler)
    outcome = poll(client, project)
    assert outcome["tasks_checked"] == 9
    assert outcome["tasks_merged"] == 0
    assert len(outcome["errors"]) == 1
    assert len(sent) == 1


def test_a_failed_batch_does_not_retry_each_pr_over_rest(
    client, app_world, monkeypatch
):
    project, _ids = seed_prs(client)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(502, json={})

    use_wire_client(monkeypatch, handler)
    outcome = poll(client, project)
    assert outcome["tasks_checked"] == outcome["tasks_merged"] == 0
    assert len(outcome["errors"]) == 10
    assert len(sent) == 1 and sent[0].url.path == "/graphql"
