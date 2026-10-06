"""A task's screen is adopted again after the backend restarts.

Recovery asks who answers each place a screen was found at. A task is a
conversation of its own and seats nobody on a roster, so the question had no
answer there ("这个项目还没有芝士"), and the refusal took the device's whole
recovery with it: no session on that machine was listened to again, and the
turns they had been given were never read.
"""

import asyncio
import uuid

from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.device_provider import DeviceChannel
from app.domain.identity.services import IdentityService
from tests.integration.conftest import open_task, post_project, room_agent_seat


def test_a_tasks_screen_is_adopted_under_its_rooms_agent(client):
    data = post_project(client, {"name": "Recover"}, owner="alice").json()["data"]
    project, room = uuid.UUID(data["id"]), data["root_topic_id"]
    seat = room_agent_seat(client, room)
    task = uuid.UUID(
        open_task(client, room, "整理一下", owner="alice", start=False)["id"]
    )
    hub = DeviceHub()
    found = {
        "sid": "task-screen",
        "screen": "birth-token",
        "command": ["claude"],
        "env": {
            "CHEESE_PROJECT": str(project),
            "CHEESE_TOPIC": str(task),
            "CHEESE_RESOURCE_ID": str(task),
        },
    }

    class Transport:
        async def send_json(self, msg):
            if msg["t"] == "session.list":
                await hub.on_device_message(
                    "center",
                    {"t": "session.result", "id": msg["id"], "value": [found]},
                )

    async def run():
        await hub.attach_device("center", Transport())
        channel = DeviceChannel(hub=hub, session_factory=client.test_factory)
        await channel.restore_screens([(project, task, "center")])
        async with client.test_factory() as session:
            agent = await IdentityService(session).ensure_room_agent_user(task)
        return hub.screen("task-screen"), agent

    screen, agent = asyncio.run(run())
    assert screen is not None
    assert screen.topic_id == task
    assert agent.username == seat
    assert screen.agent_handle == seat
