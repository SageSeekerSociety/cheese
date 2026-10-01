"""Real HTTP/PG threads: recovery, conflict, ownership and historical anchors."""

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.comment_models import DocCommentReply, DocCommentThread
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.living_doc.models import DocumentOperation
from tests.conftest import seed_user
from tests.integration.conftest import post_project, room_agent_seat


def setup(client):
    token = seed_user(client, "thread-owner")
    client.headers.update({"Authorization": f"Bearer {token}"})
    project = post_project(
        client, {"name": "Threads", "owner_handle": "thread-owner"}
    ).json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "Doc"}
    ).json()["data"]["id"]
    raw = "# 标题\r\n\r\n😀同句\r\n\r\n😀同句\r\n"
    saved = client.put(
        f"/topics/{room}/doc", json={"content": raw, "expected_version": 0}
    )
    assert saved.status_code == 200, saved.text
    node = client.get(f"/topics/{room}/docs").json()["data"]["data"][2]
    root = client.post(
        f"/topics/{room}/comments",
        json={"anchor": node["id"], "quote": "同句", "content": "原评论"},
    )
    assert root.status_code == 200, root.text
    return room, root.json()["data"], raw


def read(client, room, root):
    response = client.get(f"/topics/{room}/comments/{root['id']}/thread")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def mutation(revision, **payload):
    return {"operation_id": str(uuid.uuid4()), "expected_revision": revision, **payload}


def test_reply_resolve_reopen_replay_preserve_doc_refs_and_anchor(client):
    room, root, raw = setup(client)
    prefix = f"/topics/{room}/comments/{root['id']}"
    before_doc = client.get(f"/topics/{room}/doc").json()
    before_timeline = client.get(f"/topics/{room}/blocks").json()
    thread = read(client, room, root)
    assert (
        thread["revision"] == 1
        and thread["state"] == "open"
        and thread["replies"] == []
    )
    anchor = thread["anchor"]
    start = len("# 标题\r\n\r\n😀同句\r\n\r\n".encode())
    assert anchor == {
        "node_id": root["reply_to"],
        "quote": "同句",
        "node_content": "😀同句",
        "document_id": before_doc["data"]["id"],
        "base_version": 1,
        "start": start,
        "end": start + len("😀同句".encode()),
        "offset_unit": "utf8-bytes",
    }
    body = mutation(1, content="第一回复😀\r\n原样")
    reply = client.post(prefix + "/replies", json=body)
    assert reply.status_code == 200, reply.text
    got = reply.json()["data"]
    assert got["revision"] == 2
    assert got["replies"][0]["sequence"] == 1
    block = got["replies"][0]["comment"]
    assert block["author"] == "thread-owner" and block["reply_to"] == root["id"]
    assert block["content"] == body["content"]
    assert client.post(prefix + "/replies", json=body).json() == reply.json()
    assert (
        client.post(
            prefix + "/replies", json={**body, "content": "changed"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            prefix + "/resolve", json={k: v for k, v in body.items() if k != "content"}
        ).status_code
        == 409
    )
    assert client.get(f"/topics/{room}/comments").json()["data"]["data"] == [root]
    assert (
        client.get(f"/topics/{room}/comments/{block['id']}/thread").status_code == 404
    )
    resolve_body = mutation(2)
    resolved = client.post(prefix + "/resolve", json=resolve_body)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["data"]["state"] == "resolved"
    assert resolved.json()["data"]["replies"] == got["replies"]
    assert client.post(prefix + "/resolve", json=mutation(3)).status_code == 409
    assert (
        client.post(prefix + "/replies", json=mutation(3, content="closed")).status_code
        == 409
    )
    assert client.post(prefix + "/reopen", json=mutation(2)).status_code == 409
    reopened = client.post(prefix + "/reopen", json=mutation(3))
    assert reopened.status_code == 200 and reopened.json()["data"]["revision"] == 4
    second = client.post(prefix + "/replies", json=mutation(4, content="第二回复"))
    assert second.status_code == 200
    assert [r["sequence"] for r in second.json()["data"]["replies"]] == [1, 2]
    # The saved operation receipt is old evidence, not current thread state.
    assert client.post(prefix + "/resolve", json=resolve_body).json() == resolved.json()
    assert client.post(prefix + "/replies", json=body).json() == reply.json()
    assert read(client, room, root)["revision"] == 5
    assert client.get(f"/topics/{room}/doc").json() == before_doc
    assert client.get(f"/topics/{room}/blocks").json() == before_timeline
    changed = client.put(
        f"/topics/{room}/doc", json={"content": "另一原文", "expected_version": 1}
    )
    assert changed.status_code == 200
    reread = read(client, room, root)
    assert reread["anchor"] == anchor and reread["comment"]["anchor_quote"] == "同句"
    assert (
        reread["comment"]["reply_to"] is None
    )  # replaced node FK is gone, evidence remains
    assert len(reread["replies"]) == 2
    listed = client.get(f"/topics/{room}/comments/threads").json()["data"]
    assert listed["total"] == 1 and listed["data"][0]["reply_count"] == 2
    assert listed["data"][0]["anchor"] == anchor
    assert raw.startswith("# 标题")


@pytest.mark.parametrize("mode", ["same", "different-payload", "different-operation"])
def test_concurrent_http_claim_and_revision_have_one_reply(client, mode):
    room, root, _ = setup(client)
    prefix = f"/topics/{room}/comments/{root['id']}"
    body = mutation(1, content="reply")
    other = dict(body)
    if mode == "different-payload":
        other["content"] = "other"
    elif mode == "different-operation":
        other["operation_id"] = str(uuid.uuid4())
    barrier = threading.Barrier(2)

    def submit(payload):
        barrier.wait(timeout=10)
        return client.post(prefix + "/replies", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, [body, other]))
    assert sorted(r.status_code for r in responses) == (
        [200, 200] if mode == "same" else [200, 409]
    ), [r.text for r in responses]
    if mode == "same":
        assert responses[0].json() == responses[1].json()
    current = read(client, room, root)
    assert current["revision"] == 2 and len(current["replies"]) == 1

    async def persisted():
        async with client.test_factory() as session:
            assert (
                await session.scalar(select(func.count()).select_from(DocCommentReply))
                == 1
            )
            operations = list(
                await session.scalars(
                    select(DocumentOperation).where(
                        DocumentOperation.action == "comment-thread"
                    )
                )
            )
            assert len(operations) == 1
            assert operations[0].receipt == next(
                r.json() for r in responses if r.status_code == 200
            )

    asyncio.run(persisted())


@pytest.mark.parametrize("action", ["thread", "replies", "resolve", "reopen"])
def test_cross_room_comment_and_non_comment_are_not_thread_parents(client, action):
    room, root, _ = setup(client)
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    other = client.post(
        "/topics", json={"project_id": project, "title": "Other"}
    ).json()["data"]["id"]
    ids = [root["id"], client.get(f"/topics/{room}/doc").json()["data"]["id"]]
    for target, parent in [(other, ids[0]), (room, ids[1])]:
        path = f"/topics/{target}/comments/{parent}/{action}"
        response = (
            client.get(path)
            if action == "thread"
            else client.post(
                path,
                json=mutation(1, **({"content": "x"} if action == "replies" else {})),
            )
        )
        assert response.status_code == 404, response.text
    assert read(client, room, root)["revision"] == 1


def test_spoofed_author_extra_fields_and_unverified_actor_cannot_reply(client):
    room, root, _ = setup(client)
    path = f"/topics/{room}/comments/{root['id']}/replies"
    for extra in [
        {"author": "someone"},
        {"anchor": root["reply_to"]},
        {"refs": ["fake"]},
    ]:
        response = client.post(path, json=mutation(1, content="x", **extra))
        assert response.status_code == 400, response.text
    headers = dict(client.headers)
    client.headers.pop("Authorization")
    assert client.post(path, json=mutation(1, content="x")).status_code == 401
    client.headers.update(headers)
    assert read(client, room, root)["replies"] == []


def test_real_member_authorization_and_agent_participation_even_permissive_dev(
    client, monkeypatch
):
    room, root, _ = setup(client)
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    owner_headers = dict(client.headers)
    outsider = seed_user(client, "thread-outsider")
    client.headers.update({"Authorization": f"Bearer {outsider}"})
    prefix = f"/topics/{room}/comments/{root['id']}"
    assert client.get(prefix + "/thread").status_code == 403
    assert client.post(prefix + "/resolve", json=mutation(1)).status_code == 403
    assert (
        client.post(prefix + "/replies", json=mutation(1, content="x")).status_code
        == 403
    )
    client.headers.pop("Authorization")
    project = root["topic_id"]
    client.headers.update(owner_headers)
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    agent = room_agent_seat(client, room)
    client.headers.pop("Authorization")
    client.headers["X-Cheese-Token"] = mint_scoped_token(
        project_id=project, topic_id=room
    )
    response = client.post(prefix + "/replies", json=mutation(1, content="agent reply"))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["replies"][0]["comment"]["author"] == agent
    client.headers.pop("X-Cheese-Token")
    client.headers.update(owner_headers)
    assert read(client, room, root)["revision"] == 2


def test_legacy_comment_lazily_recovers_without_inventing_version_or_span(client):
    room, _, _ = setup(client)
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    node = client.get(f"/topics/{room}/docs").json()["data"]["data"][1]

    async def seed():
        async with client.test_factory() as session:
            old = Block(
                project_id=uuid.UUID(project),
                topic_id=uuid.UUID(room),
                kind=BlockKind.comment,
                author="old",
                author_type=AuthorType.participant,
                content="legacy",
                reply_to=uuid.UUID(node["id"]),
                anchor_quote="同句",
                refs=["old-reference"],
            )
            session.add(old)
            await session.flush()
            result = str(old.id)
            await session.commit()
            return result

    old = {"id": asyncio.run(seed())}
    thread = read(client, room, old)
    assert thread["anchor"]["node_content"] == "😀同句"
    assert thread["anchor"]["base_version"] is None
    assert thread["anchor"]["start"] is None and thread["anchor"]["end"] is None
    assert thread["comment"]["refs"] == ["old-reference"]
    response = client.post(
        f"/topics/{room}/comments/{old['id']}/resolve", json=mutation(1)
    )
    assert response.status_code == 200
    assert response.json()["data"]["comment"]["refs"] == ["old-reference"]

    async def persisted():
        async with client.test_factory() as session:
            row = await session.get(DocCommentThread, uuid.UUID(old["id"]))
            assert row is not None and row.state == "resolved" and row.revision == 2

    asyncio.run(persisted())
