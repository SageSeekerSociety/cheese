"""The migration takes the old labels out of stored compute choices.

The cloud and 「any online device」 used to be stored with a Chinese label as
their name. The code already ignores those names; this removes them from the
three places a choice lives, keeps a device's own name, and puts the labels
back on downgrade.
"""

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.core.config import settings
from app.domain.agent_session.models import AgentSession
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from tests.conftest import seed_user
from tests.integration.conftest import post_project, session_auth_headers

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_compute_choices_name_only_devices.py"
    )
)

SPECS = {"device_id": None, "cores": None, "memory_mb": None, "disk_gb": None}


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


def _load():
    spec = importlib.util.spec_from_file_location("_mig_choice_names", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _choice(name: str | None, profile: str, device_id: str | None = None) -> dict:
    return {"name": name, "profile": profile, **SPECS, "device_id": device_id}


def test_the_migration_drops_stored_labels_and_keeps_device_names(client, monkeypatch):
    pid = _project(client, monkeypatch)
    rooms = [_room(client, pid) for _ in range(4)]
    stored = {
        "cloud": _choice("云端 · 标准配置", "cloud"),
        "custom": {**_choice("云端 · 自定义配置", "cloud"), "cores": 8},
        "any_device": _choice("自有设备 · 自动选择", "device"),
        "lab": _choice("实验室工作站", "device", "lab"),
        # Written when a bound device's name could not be found.
        "unknown_device": _choice("自有设备", "device", "gone"),
    }
    project_settings = {"compute_configs": {"default": stored["any_device"]}, "x": 1}
    room_choices = dict(
        zip(rooms, ("cloud", "custom", "lab", "unknown_device"), strict=True)
    )

    async def seed() -> list[uuid.UUID]:
        async with client.test_factory() as s:
            # Raw JSON: the model would normalize the labels on the way in.
            await s.execute(
                text("UPDATE projects SET settings = CAST(:v AS json) WHERE id = :id"),
                {"v": json.dumps(project_settings), "id": uuid.UUID(pid)},
            )
            for rid, key in room_choices.items():
                await s.execute(
                    text(
                        "UPDATE topics SET compute_config = CAST(:v AS json) "
                        "WHERE id = :id"
                    ),
                    {"v": json.dumps(stored[key]), "id": uuid.UUID(rid)},
                )
            sessions = [
                AgentSession(
                    conversation_id=uuid.UUID(rid),
                    agent_handle="cheese",
                    harness="claude-code",
                    execution_request={"choice": stored[key], "kept": True},
                )
                for rid, key in room_choices.items()
            ]
            s.add_all(sessions)
            await s.commit()
            return [row.id for row in sessions]

    session_ids = asyncio.run(seed())

    def run(step: str):
        def apply(conn) -> None:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(_load(), step)()

        async def go() -> tuple[dict, list[dict], list[dict]]:
            async with client.test_factory() as s:
                await (await s.connection()).run_sync(apply)
                await s.commit()
            async with client.test_factory() as s:
                project = await s.get(Project, uuid.UUID(pid))
                topics = [
                    (await s.get(Topic, uuid.UUID(r))).compute_config for r in rooms
                ]
                requests = [
                    (await s.get(AgentSession, i)).execution_request
                    for i in session_ids
                ]
                return project.settings, topics, requests

        return asyncio.run(go())

    settings_after, topics_after, requests_after = run("upgrade")

    assert settings_after["x"] == 1
    assert "name" not in settings_after["compute_configs"]["default"]
    names = [choice.get("name") for choice in topics_after]
    assert names == [None, None, "实验室工作站", None]
    assert [r["choice"].get("name") for r in requests_after] == names
    assert all(r["kept"] for r in requests_after)
    assert topics_after[1]["cores"] == 8

    settings_back, topics_back, _ = run("downgrade")
    assert settings_back["compute_configs"]["default"]["name"] == "自有设备 · 自动选择"
    assert [choice["name"] for choice in topics_back] == [
        "云端 · 标准配置",
        "云端 · 自定义配置",
        "实验室工作站",
        "自有设备",
    ]
