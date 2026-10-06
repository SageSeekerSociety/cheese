"""A compute choice names only a device; the platform's choices carry no name.

The cloud and 「any online device」 used to be
stored with a Chinese label as their name, and every member of a project saw it
whatever language their screen was in. They are identified by their fields now
and each screen renders the label, so the API hands out no name for them — not
even when a row still holds one of the old labels.
"""

import asyncio
import json
import uuid

from sqlalchemy import text

from app.core.config import settings
from app.domain.agent.compute_configs import ComputeChoice
from tests.conftest import seed_user
from tests.integration.conftest import post_project, session_auth_headers

SPECS = {"device_id": None}


def _project(client, monkeypatch) -> str:
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'names_owner')}"
    response = post_project(client, json={"name": "Names"}, owner="names_owner")
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _room(client, pid: str) -> str:
    response = client.post(
        "/topics",
        json={"project_id": pid, "title": "Room"},
        headers=session_auth_headers("names_owner"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_the_cloud_is_handed_out_without_a_name(client, monkeypatch):
    pid = _project(client, monkeypatch)
    configs = client.get(f"/projects/{pid}/compute-configs").json()["data"]
    assert configs["default"]["profile"] == "cloud"
    assert configs["default"]["name"] is None

    # A client that still sends a label for the cloud does not get it stored.
    rid = _room(client, pid)
    sent = {"name": "云端 · 标准配置", "profile": "cloud", **SPECS}
    response = client.put(f"/topics/{rid}/compute-profile", json={"choice": sent})
    assert response.status_code == 200, response.text
    choice = client.get(f"/topics/{rid}/compute-profile").json()["data"]["choice"]
    assert (choice["profile"], choice["name"]) == ("cloud", None)


def test_a_named_device_keeps_its_name_and_any_device_has_none():
    lab = ComputeChoice(name=" 实验室工作站 ", profile="device", device_id="lab")
    assert lab.name == "实验室工作站"
    auto = ComputeChoice(name="自有设备 · 自动选择", profile="device")
    assert auto.name is None


def _choice(name: str | None, profile: str, device_id: str | None = None) -> dict:
    return {"name": name, "profile": profile, **SPECS, "device_id": device_id}


def test_a_stored_label_on_a_platform_choice_is_not_handed_out(client, monkeypatch):
    pid = _project(client, monkeypatch)
    cloud_room = _room(client, pid)
    legacy = {
        "project": _choice("自有设备 · 自动选择", "device"),
        cloud_room: _choice("云端 · 标准配置", "cloud"),
    }

    async def seed() -> None:
        async with client.test_factory() as s:
            # Raw JSON, as rows written before choices lost their labels hold it.
            await s.execute(
                text("UPDATE projects SET settings = CAST(:v AS json) WHERE id = :id"),
                {
                    "v": json.dumps(
                        {"compute_configs": {"default": legacy["project"]}}
                    ),
                    "id": uuid.UUID(pid),
                },
            )
            for rid in (cloud_room,):
                await s.execute(
                    text(
                        "UPDATE topics SET compute_config = CAST(:v AS json) "
                        "WHERE id = :id"
                    ),
                    {"v": json.dumps(legacy[rid]), "id": uuid.UUID(rid)},
                )
            await s.commit()

    asyncio.run(seed())

    default = client.get(f"/projects/{pid}/compute-configs").json()["data"]["default"]
    assert (default["profile"], default["name"]) == ("device", None)
    for rid in (cloud_room,):
        choice = client.get(f"/topics/{rid}/compute-profile").json()["data"]["choice"]
        assert (choice["profile"], choice["name"]) == ("cloud", None)
