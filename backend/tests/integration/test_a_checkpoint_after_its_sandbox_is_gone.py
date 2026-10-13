"""A turn that ends after its sandbox was destroyed while idle has nothing on a
machine to push. Its checkpoint is answered as done, asks no host, and does not
bring a sandbox back. The hosts and the pool are the ones of
``test_sandbox_idle_stop``."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from app.domain.agent import execution
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.user.models import User
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    home_of,
    run,
    sweep,
    time_passes,
    tool_call,
    working_on,
)

CHECKPOINT = {
    "method": "control",
    "params": {"subtype": "checkpoint", "request_id": "checkpoint-1"},
}


def _a_cloud_machine(case, device_id: str) -> None:
    """The platform's record of the pool host's machine, as a host that
    enrolled has one: what the execution route checks a call's machine by."""

    async def add():
        async with case.client.test_request_factory() as db:
            owner = await db.scalar(select(User.id).where(User.username == "alice"))
            db.add(
                DeviceRow(
                    device_id=device_id,
                    name=device_id,
                    token=f"{device_id}-token",
                    owner_user_id=owner,
                    supply=Supply.cloud,
                    created_at=datetime.now(UTC),
                )
            )
            await db.commit()

    run(case, add)


def test_a_checkpoint_after_the_sandbox_was_destroyed_asks_no_host(cloud, monkeypatch):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    _a_cloud_machine(cloud, "host-a")
    hands = tool_call(cloud, seat)
    asked = []

    async def recording(target, method, params, **kwargs):
        asked.append((target["device_id"], method, params.get("subtype")))
        return await cloud.hosts.executor(target, method, params, **kwargs)

    monkeypatch.setattr(execution, "call", recording)

    def checkpoint():
        return cloud.client.post(
            urlsplit(hands["target"]["url"]).path,
            headers={"X-Cheese-Token": hands["token"]},
            json=CHECKPOINT,
        )

    while_it_lives = checkpoint()
    assert while_it_lives.status_code == 200, while_it_lives.text
    assert asked == [("host-a", "control", "checkpoint")]

    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1
    asked.clear()

    after = checkpoint()
    assert after.status_code == 200, after.text
    assert after.json() == {}
    assert asked == []
    assert home_of(cloud, seat) is None
