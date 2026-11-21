"""A task in a project whose repository is someone else's (GitHub) keeps what
its checkout had not committed with the platform, not in that repository.

The machine hands the snapshot in as a git bundle (`cheese keep`); the platform
keeps it in its private bucket, gives it back to a new checkout of the task,
and answers from it what became of the work while no sandbox holds the task:
the task still holds uncommitted changes, handing it in says they were kept,
and its files read as the committed version, since a bundle has no files to
read out of it.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.core.sentences import say
from app.core.storage import S3StorageBackend
from app.domain.machine import hand_in
from app.domain.project.models import ProjectForge
from app.domain.repository import kept_work
from app.domain.room_task.models import Task
from tests.integration.test_a_persons_command_acts_on_the_environments_authority import (
    _alice_attached,
    _from_the_machine,
)
from tests.integration.test_file_panel_safety import _owner
from tests.integration.test_task_files_on_its_own_machine import (
    _channel_with_released_older_task,
    _published,
    _seat,
)
from tests.integration.test_task_files_on_its_own_machine import (
    machines as machines,
)
from tests.machine_work import machine_bundles, machine_commits
from tests.support.files_bucket import files_bucket


@pytest.fixture
def bucket(monkeypatch):
    with files_bucket() as config:
        storage = S3StorageBackend(
            bucket=config["files_bucket"],
            endpoint_url=config["files_bucket_endpoint_url"],
            access_key="test",
            secret_key="test",
        )
        monkeypatch.setattr(kept_work, "private_storage", lambda: storage)
        yield storage


def _on_github(client, pid) -> None:
    async def bind():
        async with client.test_factory() as session:
            await session.execute(
                update(ProjectForge)
                .where(ProjectForge.project_id == pid)
                .values(kind="github_app")
            )
            await session.commit()

    asyncio.run(bind())


def _as(pid, conversation) -> dict[str, str]:
    token = mint_scoped_token(project_id=str(pid), topic_id=str(conversation))
    return {"Authorization": f"Bearer {token}"}


def _kept(client, pid, task, headers, method="get", body=None, cut_from=None):
    return client.request(
        method,
        f"/projects/{pid}/git/tasks/{task.id}/kept",
        params={"cut_from": cut_from} if cut_from else None,
        content=body,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )


def _task_with_kept_work(client):
    pid, room, task = _channel_with_released_older_task(client)
    assert task is not None
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    _on_github(client, pid)
    bundle, commit, head = machine_bundles(
        pid,
        task.id,
        {"backend/usage/models.py": "POOL = 2\n", "notes/draft.md": "draft\n"},
    )
    kept = _kept(client, pid, task, _as(pid, task.id), "put", bundle, head)
    assert kept.status_code == 200, kept.text
    return pid, room, task, bundle, commit


async def _holds_uncommitted(client, task) -> bool:
    async with client.test_factory() as session:
        row = await session.get(Task, task.id)
        return await hand_in.uncommitted(session, row)


def test_a_new_checkout_gets_back_the_snapshot_the_task_handed_in(
    client, bucket, machines
):
    pid, _, task, bundle, _ = _task_with_kept_work(client)
    mine = _as(pid, task.id)

    fetched = _kept(client, pid, task, mine)
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == bundle

    assert _kept(client, pid, task, mine, "delete").status_code == 200
    assert _kept(client, pid, task, mine).status_code == 204


def test_another_tasks_session_cannot_read_or_replace_the_snapshot(
    client, bucket, machines
):
    pid, room, task, bundle, _ = _task_with_kept_work(client)
    other = _as(pid, uuid.uuid4())

    assert _kept(client, pid, task, other).status_code in (403, 404)
    assert _kept(client, pid, task, other, "put", b"x", "0" * 40).status_code in (
        403,
        404,
    )
    assert _kept(client, pid, task, other, "delete").status_code in (403, 404)
    assert _kept(client, pid, task, _as(pid, task.id)).content == bundle


def test_a_task_whose_sandbox_is_gone_holds_its_kept_changes_and_reads_committed(
    client, bucket, machines
):
    _, asked = machines
    pid, room, task, _, _ = _task_with_kept_work(client)
    _seat(client, task, device="gone", lost=True)

    read = client.get(
        f"/projects/{pid}/file",
        params={
            "source": "live",
            "path": "backend/usage/models.py",
            "topic": str(room),
            "task": str(task.id),
        },
        headers=_owner(client),
    )

    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "POOL = 1\n"
    assert read.json()["data"]["source"] == "committed"
    assert asyncio.run(_holds_uncommitted(client, task)) is True
    handed = client.post(
        f"/topics/{task.id}/hand-in",
        json={"subject": "Hand in the kept edit"},
        headers=_owner(client),
    )
    assert handed.status_code == 503, handed.text
    assert handed.json()["error"]["message"] == say("handInSandboxReleasedKept")
    assert asked == []


def test_a_clean_checkout_leaves_the_task_holding_nothing(client, bucket, machines):
    pid, _, task, _, _ = _task_with_kept_work(client)
    _seat(client, task, device="gone", lost=True)

    _kept(client, pid, task, _as(pid, task.id), "delete")

    assert asyncio.run(_holds_uncommitted(client, task)) is False


@pytest.mark.parametrize("named", [True, False], ids=["person", "environment"])
def test_the_tasks_own_environment_keeps_its_snapshot(
    client, bucket, machines, monkeypatch, named
):
    # A commit in a person's terminal there runs `cheese sync`, which keeps
    # what is left uncommitted on the environment's authority.
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    pid, _, task, _, _ = _task_with_kept_work(client)
    device, attachment = _alice_attached(client, task.id)
    bundle, _, head = machine_bundles(pid, task.id, {"notes/draft.md": "later\n"})

    kept = client.put(
        f"/machine/api/projects/{pid}/git/tasks/{task.id}/kept",
        params={"cut_from": head},
        content=bundle,
        headers=_from_the_machine(device, attachment if named else None),
    )

    assert kept.status_code == 200, kept.text
    assert _kept(client, pid, task, _as(pid, task.id)).content == bundle
