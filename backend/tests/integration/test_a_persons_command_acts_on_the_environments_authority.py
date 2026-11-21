"""A command a person starts in an environment is answered on the
environment's authority, in the person's name — never with the person's own
roles.

- it cannot accept a hand-in, even one waiting on that very person;
- it cannot change the project's settings, though that person may;
- opening the task's files from it does not make anybody the task's author;
- a request from the environment that names nobody is no more than that.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.environment.models import (
    READY,
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.delivery import delivery_task_id
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_accept import _make_card
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_authorization import _make_project, _make_topic
from tests.integration.test_accept_pr import _rendered_head
from tests.integration.test_accept_pr import app_world as app_world

STATE = "/environment/state"


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")


def _alice_attached(client, task_id: uuid.UUID) -> tuple[str, str]:
    """The task's environment, ready on a machine, with alice attached to it
    from the task. Answers the machine and her attachment."""
    device = f"machine-{uuid.uuid4().hex[:8]}"

    async def seed():
        async with client.test_factory() as db:
            task = await db.get(Task, task_id)
            room = await db.get(Topic, task.room_id)
            alice = await db.scalar(select(User).where(User.username == "alice"))
            environment = Environment(
                id=uuid.uuid4(),
                topic_id=room.id,
                project_id=room.project_id,
                device_id=device,
                resource_id=str(uuid.uuid4()),
                room_resource_id=str(room.resource_id or room.id),
                choice={"kind": "sandbox", "spec": "standard"},
                status=READY,
                executor={"state": STATE, "platform": True},
            )
            db.add(environment)
            await db.flush()
            db.add(
                ConversationEnvironment(
                    conversation_id=task_id, environment_id=environment.id
                )
            )
            attachment = EnvironmentAttachment(
                id=uuid.uuid4(),
                environment_id=environment.id,
                user_id=alice.id,
                conversation_id=task_id,
                generation=str(uuid.uuid4()),
            )
            db.add(attachment)
            await db.commit()
            return str(attachment.id)

    return device, client.portal.call(seed)


def _from_the_machine(device: str, attachment: str | None) -> dict:
    return {
        "x-device-connection-secret": "owner-secret",
        "x-machine-device": device,
        "x-machine-state": STATE,
        **({"x-cheese-attachment": attachment} if attachment else {}),
    }


@pytest.mark.parametrize("named", [True, False], ids=["person", "environment"])
def test_it_cannot_accept_a_hand_in_waiting_on_that_person(client, named):
    project = _make_project(client)
    room = _make_topic(client, project)
    card = _make_card(client, room, "alice")
    device, attachment = _alice_attached(client, delivery_task_id(client, room))

    answer = client.post(
        f"/machine/api/accept-cards/{card}/accept",
        headers=_from_the_machine(device, attachment if named else None),
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card)},
    )

    assert answer.status_code in (401, 403), answer.text
    cards = client.get(f"/topics/{room}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"


@pytest.mark.parametrize("named", [True, False], ids=["person", "environment"])
def test_it_cannot_change_the_settings_that_person_may(client, named):
    # alice owns the project: its settings are hers to change.
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"][
        "id"
    ]
    room = _make_topic(client, project)
    device, attachment = _alice_attached(client, delivery_task_id(client, room))

    answer = client.put(
        f"/machine/api/projects/{project}/task-naming",
        headers=_from_the_machine(device, attachment if named else None),
        json={"mode": "manual"},
    )

    assert answer.status_code in (401, 403, 404), answer.text
    mine = session_auth_headers("alice")
    now = client.get(f"/projects/{project}/task-naming", headers=mine)
    assert now.json()["data"]["mode"] != "manual"
    # She may change them herself.
    by_her = client.put(
        f"/projects/{project}/task-naming", headers=mine, json={"mode": "manual"}
    )
    assert by_her.status_code == 200, by_her.text


def test_opening_the_task_from_it_makes_nobody_its_author(client):
    project = _make_project(client)
    room = _make_topic(client, project)
    task_id = delivery_task_id(client, room)
    device, attachment = _alice_attached(client, task_id)

    client.post(
        f"/machine/api/projects/{project}/git/tasks/{task_id}",
        headers=_from_the_machine(device, attachment),
    )

    async def author():
        async with client.test_factory() as db:
            return await db.scalar(select(Task.author_handle).where(Task.id == task_id))

    assert client.portal.call(author) is None


def test_a_person_no_longer_on_the_project_is_nobody_there(client):
    project = _make_project(client)
    room = _make_topic(client, project)
    task_id = delivery_task_id(client, room)
    device, attachment = _alice_attached(client, task_id)

    async def leave():
        async with client.test_factory() as db:
            # The attachment is of somebody who is not on the project.
            found = await db.get(EnvironmentAttachment, uuid.UUID(attachment))
            name = f"s{uuid.uuid4().hex[:6]}"
            stranger = User(
                username=name,
                email=f"{name}@example.test",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(stranger)
            await db.flush()
            found.user_id = stranger.id
            await db.commit()

    client.portal.call(leave)

    answer = client.get(
        f"/machine/api/topics/{task_id}", headers=_from_the_machine(device, attachment)
    )
    assert answer.status_code == 403, answer.text
