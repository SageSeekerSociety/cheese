"""What a person's own 芝士 remembers about them.

It is kept on the platform's memory files in a scope of the person's own: 芝士
saves to it in their panel, the next conversation starts with its index, the
person reads, edits, deletes and turns it off on their settings page, and
nothing of it reaches a project.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select

from app.api.routes import assistant_agent
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.assistant.models import AssistantChange
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import memory_index
from tests.conftest import seed_user
from tests.integration.conftest import new_project
from tests.integration.test_assistant import _auth, _events, _ledger_user
from tests.integration.test_assistant import gateway as gateway  # the fixture

FORMAL = {
    "action": "save",
    "name": "formal-to-teachers",
    "description": "给老师写东西用书面语",
    "type": "feedback",
    "text": "给老师写的消息和邮件用书面语，不用「哈」「呀」。她纠正过一次。",
}


def _acting(client, handle: str, work: str | None = None) -> dict:
    seed_user(client, handle)
    return {
        "Authorization": f"Bearer {
            mint_delegated_credential(
                user_id=_ledger_user(client, handle)[0],
                handle=handle,
                work=work or str(uuid.uuid4()),
                ttl_s=300,
                read_only=False,
            )
        }"
    }


def _remember(client, acting: dict, **fields) -> dict:
    r = client.post(
        "/assistant/agent/memory", json={**FORMAL, **fields}, headers=acting
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _memory(client, me: dict) -> dict:
    r = client.get("/assistant/memory", headers=me)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_what_cheese_saves_is_on_the_settings_page_once_per_name(client):
    me, acting = _auth(client, "alice"), _acting(client, "alice")

    assert _remember(client, acting)["text"] == "已记住：给老师写东西用书面语"
    _remember(client, acting, text="给老师写东西用书面语，称呼用「老师」。")

    [item] = _memory(client, me)["items"]
    assert item["description"] == "给老师写东西用书面语"
    assert item["text"] == "给老师写东西用书面语，称呼用「老师」。"
    read = client.post(
        "/assistant/agent/memory",
        json={"action": "read", "name": "formal-to-teachers"},
        headers=acting,
    )
    assert "称呼用「老师」" in read.json()["data"]["text"]


def test_a_saved_memory_is_undone_from_the_panel(client):
    me, acting = _auth(client, "alice"), _acting(client, "alice")
    _remember(client, acting)

    async def change_id():
        async with client.test_factory() as db:
            return await db.scalar(
                select(AssistantChange.id).where(AssistantChange.kind == "memory")
            )

    r = client.post(
        f"/assistant/changes/{asyncio.run(change_id())}/undo", json={}, headers=me
    )

    assert r.status_code == 200, r.text
    assert _memory(client, me)["items"] == []


def test_the_person_edits_deletes_and_clears_what_is_remembered(client):
    me, acting = _auth(client, "alice"), _acting(client, "alice")
    _remember(client, acting)
    _remember(client, acting, name="ta-os-course", description="在带操作系统助教课")

    edited = client.put(
        "/assistant/memory/formal-to-teachers",
        json={"description": "给老师写东西要正式", "text": "用书面语。"},
        headers=me,
    ).json()["data"]
    assert [i["description"] for i in edited["items"]] == [
        "给老师写东西要正式",
        "在带操作系统助教课",
    ]
    left = client.delete("/assistant/memory/ta-os-course", headers=me).json()["data"]
    assert [i["name"] for i in left["items"]] == ["formal-to-teachers"]
    assert client.delete("/assistant/memory", headers=me).json()["data"]["items"] == []


def test_cheese_cannot_reach_the_settings_page_of_its_memory(client):
    acting = _acting(client, "alice")
    assert client.get("/assistant/memory", headers=acting).status_code in (401, 403)
    assert client.delete("/assistant/memory", headers=acting).status_code in (401, 403)


def test_turned_off_nothing_is_saved_and_what_is_kept_stays(client):
    me, acting = _auth(client, "alice"), _acting(client, "alice")
    _remember(client, acting)

    off = client.put("/assistant/memory", json={"enabled": False}, headers=me)
    assert off.json()["data"]["enabled"] is False
    r = client.post(
        "/assistant/agent/memory", json={**FORMAL, "name": "x"}, headers=acting
    )

    assert r.status_code == 400
    assert [i["name"] for i in _memory(client, me)["items"]] == ["formal-to-teachers"]


def test_a_routine_run_does_not_save(client, monkeypatch):
    acting = _acting(client, "alice")

    async def nobody_there(questions, work):
        return True

    monkeypatch.setattr(assistant_agent, "_unattended", nobody_there)
    r = client.post("/assistant/agent/memory", json=FORMAL, headers=acting)
    assert r.status_code == 400
    assert _memory(client, _auth(client, "alice"))["items"] == []


def test_a_new_conversation_starts_with_what_is_remembered(client, gateway):
    me, acting = _auth(client, "alice"), _acting(client, "alice")
    _remember(client, acting)
    conversation = client.post("/assistant/conversations", headers=me).json()["data"][
        "id"
    ]
    gateway.script = [("text", "好的。")]

    r = client.post(
        f"/assistant/conversations/{conversation}/ask",
        json={"question": "帮我看看待办"},
        headers=me,
    )

    assert r.status_code == 200, r.text
    assert _events(r.text)[-1][0] == "done"
    told = json.dumps(gateway.requests[0], ensure_ascii=False)
    assert "给老师写东西用书面语" in told


def test_nothing_personal_reaches_a_project(client):
    me, acting = _auth(client, "alice"), _acting(client, "alice")
    _remember(client, acting)
    project = new_project(client, name="组会", owner="alice")

    listed = client.get(
        "/memory/files",
        params={
            "project_id": project["id"],
            "scope": "personal",
            "owner_handle": "alice",
        },
        headers=me,
    )
    assert listed.status_code in (400, 422)

    async def index():
        async with client.test_factory() as db:
            return await memory_index(
                db, uuid.UUID(project["id"]), speaker_handles=["alice"]
            )

    got = asyncio.run(index())
    assert all(
        s.prefix.split("/")[0] != MemoryFileScope.personal.value for s in got.sections
    )
    assert "书面语" not in json.dumps(
        [s.text for s in got.sections], ensure_ascii=False
    )


def test_a_personal_memory_is_not_pruned_through_a_project(client):
    """The project memory panel prunes by id; a person's own memory is in no
    project, and its id is answered there as one that does not exist."""
    me, acting = _auth(client, "alice"), _acting(client, "alice")
    _remember(client, acting)

    async def ids():
        async with client.test_factory() as db:
            from app.domain.memory.models import MemoryFileRecord

            return [
                str(i)
                for i in await db.scalars(
                    select(MemoryFileRecord.id).where(
                        MemoryFileRecord.scope == MemoryFileScope.personal
                    )
                )
            ]

    for row_id in asyncio.run(ids()):
        assert client.delete(f"/memory/{row_id}", headers=me).status_code == 404
    assert [i["name"] for i in _memory(client, me)["items"]] == ["formal-to-teachers"]
