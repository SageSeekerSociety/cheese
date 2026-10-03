"""A teammate nobody has named is flagged, so each screen names it in its
reader's language instead of showing the stored 「芝士」.

The stored name stays 芝士: agents and prompts read it. A name a person gives
it, at creation or later, ends that. Every listing that names a teammate (the
project's agents, its roster, a room's roster) carries the flag beside the
name.

The migration that adds the flag is run as ``alembic upgrade`` runs it, on rows
written before it: an agent still called 芝士 becomes ``default`` and every
other one ``human``.
"""

import asyncio
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.integration.conftest import post_project

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "da05dacf50ae_agent_name_source.py"
)


def _agents(client, project_id: str) -> list[dict]:
    return client.get(f"/projects/{project_id}/agents").json()["data"]["data"]


def _roster_agents(client, project_id: str) -> list[dict]:
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    return [row for row in rows if row.get("agent")]


def _room_agents(client, project_id: str) -> list[dict]:
    room = client.post(
        "/topics", json={"project_id": project_id, "title": "讨论"}
    ).json()["data"]
    rows = client.get(f"/topics/{room['id']}/members").json()["data"]["data"]
    return [row for row in rows if row.get("agent")]


def test_a_project_s_first_teammate_is_unnamed_everywhere_it_is_listed(client):
    project = post_project(client, json={"name": "P"}).json()["data"]

    [agent] = _agents(client, project["id"])
    [member] = _roster_agents(client, project["id"])
    [seat] = _room_agents(client, project["id"])

    assert (agent["display_name"], agent["name_source"]) == ("芝士", "default")
    assert (member["name"], member["name_source"]) == ("芝士", "default")
    assert (seat["name"], seat["name_source"]) == ("芝士", "default")


def test_a_name_chosen_when_the_project_is_made_is_a_person_s(client):
    project = post_project(client, json={"name": "P", "agent_name": "Moss"}).json()[
        "data"
    ]

    [agent] = _agents(client, project["id"])

    assert (agent["display_name"], agent["name_source"]) == ("Moss", "human")


def test_renaming_ends_it_and_editing_settings_does_not(client):
    project = post_project(client, json={"name": "P"}).json()["data"]
    [agent] = _agents(client, project["id"])
    url = f"/projects/{project['id']}/agents/{agent['id']}"

    kept = client.put(url, json={"configuration": agent["configuration"]})
    assert kept.status_code == 200, kept.text
    assert _agents(client, project["id"])[0]["name_source"] == "default"

    renamed = client.put(url, json={"display_name": "芝士"})
    assert renamed.status_code == 200, renamed.text
    [after] = _agents(client, project["id"])
    [member] = _roster_agents(client, project["id"])
    assert (after["display_name"], after["name_source"]) == ("芝士", "human")
    assert member["name_source"] == "human"


def test_a_teammate_added_without_a_name_is_unnamed_and_one_with_a_name_is_not(
    client,
):
    project = post_project(client, json={"name": "P"}).json()["data"]
    base = f"/projects/{project['id']}/agents"
    configuration = _agents(client, project["id"])[0]["configuration"]

    unnamed = client.post(
        base, json={"display_name": "", "configuration": configuration}
    ).json()["data"]
    named = client.post(
        base, json={"display_name": "评审", "configuration": configuration}
    ).json()["data"]

    assert (unnamed["display_name"], unnamed["name_source"]) == ("芝士", "default")
    assert (named["display_name"], named["name_source"]) == ("评审", "human")


def _upgrade(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_agent_name", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            await s.execute(text("ALTER TABLE agent_instances DROP COLUMN name_source"))
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def test_the_migration_marks_teammates_still_called_by_the_default_name(client):
    untouched = post_project(client, json={"name": "A"}).json()["data"]
    renamed = post_project(client, json={"name": "B"}).json()["data"]
    [agent] = _agents(client, renamed["id"])
    client.put(
        f"/projects/{renamed['id']}/agents/{agent['id']}", json={"display_name": "Moss"}
    )

    _upgrade(client)

    assert _agents(client, untouched["id"])[0]["name_source"] == "default"
    assert _agents(client, renamed["id"])[0]["name_source"] == "human"
