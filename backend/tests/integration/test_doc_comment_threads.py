"""Real HTTP/PG threads: recovery, conflict and ownership."""

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.living_doc.models import DocumentComment, DocumentOperation
from tests.conftest import seed_user
from tests.integration.conftest import post_project, room_agent_seat
from tests.support.living_doc import document_of


def setup(client):
    token = seed_user(client, "thread-owner")
    client.headers.update({"Authorization": f"Bearer {token}"})
    project = post_project(client, {"name": "Threads"}, owner="thread-owner").json()[
        "data"
    ]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "Doc"}
    ).json()["data"]["id"]
    doc = document_of(client, room)
    raw = "# 标题\r\n\r\n😀同句\r\n\r\n😀同句\r\n"
    saved = client.put(
        f"/documents/{doc}", json={"content": raw, "expected_version": 0}
    )
    assert saved.status_code == 200, saved.text
    root = client.post(
        f"/documents/{doc}/comments", json={"quote": "同句", "content": "原评论"}
    )
    assert root.status_code == 200, root.text
    return room, doc, root.json()["data"], raw


def read(client, doc, root):
    response = client.get(f"/documents/{doc}/comments/{root['id']}/thread")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def mutation(revision, **payload):
    return {"operation_id": str(uuid.uuid4()), "expected_revision": revision, **payload}


def test_reply_resolve_reopen_replay_and_leave_the_document_alone(client):
    room, doc, root, raw = setup(client)
    prefix = f"/documents/{doc}/comments/{root['id']}"
    before_doc = client.get(f"/documents/{doc}").json()
    before_timeline = client.get(f"/topics/{room}/blocks").json()
    thread = read(client, doc, root)
    assert (
        thread["revision"] == 1
        and thread["state"] == "open"
        and thread["replies"] == []
    )
    body = mutation(1, content="第一回复😀\r\n原样")
    reply = client.post(prefix + "/replies", json=body)
    assert reply.status_code == 200, reply.text
    got = reply.json()["data"]
    assert got["revision"] == 2
    assert got["replies"][0]["sequence"] == 1
    block = got["replies"][0]["comment"]
    assert block["author"] == "thread-owner"
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
    listed = client.get(f"/documents/{doc}/comments/threads").json()["data"]["data"]
    assert [t["comment"]["id"] for t in listed] == [root["id"]]
    assert (
        client.get(f"/documents/{doc}/comments/{block['id']}/thread").status_code == 404
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
    assert read(client, doc, root)["revision"] == 5
    assert client.get(f"/documents/{doc}").json() == before_doc
    assert client.get(f"/topics/{room}/blocks").json() == before_timeline
    changed = client.put(
        f"/documents/{doc}", json={"content": "另一原文", "expected_version": 1}
    )
    assert changed.status_code == 200
    reread = read(client, doc, root)
    assert reread["comment"]["anchor_quote"] == "同句"
    assert len(reread["replies"]) == 2
    listed = client.get(f"/documents/{doc}/comments/threads").json()["data"]
    assert listed["total"] == 1
    assert [r["comment"]["content"] for r in listed["data"][0]["replies"]] == [
        "第一回复😀\r\n原样",
        "第二回复",
    ]
    assert listed["data"][0]["answering"] is None
    assert raw.startswith("# 标题")


@pytest.mark.parametrize("mode", ["same", "different-payload", "different-operation"])
def test_concurrent_http_claim_and_revision_have_one_reply(client, mode):
    room, doc, root, _ = setup(client)
    prefix = f"/documents/{doc}/comments/{root['id']}"
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
    current = read(client, doc, root)
    assert current["revision"] == 2 and len(current["replies"]) == 1

    async def persisted():
        async with client.test_factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DocumentComment)
                    .where(DocumentComment.thread_id.is_not(None))
                )
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
    room, doc, root, _ = setup(client)
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    other = client.post(
        "/topics", json={"project_id": project, "title": "Other"}
    ).json()["data"]["id"]
    other_doc = document_of(client, other)
    # Another document's thread, and the document itself (not a comment).
    for target, parent in [(other_doc, root["id"]), (doc, doc)]:
        path = f"/documents/{target}/comments/{parent}/{action}"
        response = (
            client.get(path)
            if action == "thread"
            else client.post(
                path,
                json=mutation(1, **({"content": "x"} if action == "replies" else {})),
            )
        )
        assert response.status_code == 404, response.text
    assert read(client, doc, root)["revision"] == 1


def test_spoofed_author_extra_fields_and_unverified_actor_cannot_reply(client):
    room, doc, root, _ = setup(client)
    path = f"/documents/{doc}/comments/{root['id']}/replies"
    for extra in [
        {"author": "someone"},
        {"anchor": "elsewhere"},
        {"refs": ["fake"]},
    ]:
        response = client.post(path, json=mutation(1, content="x", **extra))
        assert response.status_code == 400, response.text
    headers = dict(client.headers)
    client.headers.pop("Authorization")
    assert client.post(path, json=mutation(1, content="x")).status_code == 401
    client.headers.update(headers)
    assert read(client, doc, root)["replies"] == []


def test_real_member_authorization_and_agent_participation_even_permissive_dev(
    client, monkeypatch
):
    room, doc, root, _ = setup(client)
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    owner_headers = dict(client.headers)
    outsider = seed_user(client, "thread-outsider")
    client.headers.update({"Authorization": f"Bearer {outsider}"})
    prefix = f"/documents/{doc}/comments/{root['id']}"
    assert client.get(prefix + "/thread").status_code == 403
    assert client.post(prefix + "/resolve", json=mutation(1)).status_code == 403
    assert (
        client.post(prefix + "/replies", json=mutation(1, content="x")).status_code
        == 403
    )
    client.headers.pop("Authorization")
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
    assert read(client, doc, root)["revision"] == 2


@pytest.mark.parametrize("pair", [("reply", "resolve"), ("resolve", "resolve")])
def test_competing_reply_and_resolution_never_claim_both_succeeded(client, pair):
    room, doc, root, _ = setup(client)
    prefix = f"/documents/{doc}/comments/{root['id']}"
    barrier = threading.Barrier(2)

    def submit(action):
        body = mutation(1, **({"content": "racing reply"} if action == "reply" else {}))
        barrier.wait(timeout=10)
        return client.post(
            prefix + ("/replies" if action == "reply" else "/resolve"), json=body
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, pair))
    assert sorted(r.status_code for r in responses) == [200, 409], [
        r.text for r in responses
    ]
    current = read(client, doc, root)
    winner = pair[next(i for i, r in enumerate(responses) if r.status_code == 200)]
    assert current["revision"] == 2
    assert current["state"] == ("open" if winner == "reply" else "resolved")
    assert len(current["replies"]) == (1 if winner == "reply" else 0)


def test_same_operation_cannot_move_to_another_root_and_replay_requires_membership(
    client, monkeypatch
):
    from sqlalchemy import delete

    from app.domain.topic.models import Topic, TopicMembership, TopicRole

    room, doc, root, _ = setup(client)
    prefix = f"/documents/{doc}/comments/{root['id']}"
    owner_headers = dict(client.headers)
    participant_token = seed_user(client, "thread-member")

    async def join():
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            assert topic is not None
            topic.is_private = True
            session.add(
                TopicMembership(
                    topic_id=uuid.UUID(room),
                    member_handle="thread-member",
                    role=TopicRole.member,
                )
            )
            await session.commit()

    asyncio.run(join())
    other = client.post(
        f"/documents/{doc}/comments", json={"content": "another root"}
    ).json()["data"]
    client.headers.update({"Authorization": f"Bearer {participant_token}"})
    body = mutation(1, content="member reply")
    response = client.post(prefix + "/replies", json=body)
    assert response.status_code == 200, response.text
    assert (
        client.post(
            f"/documents/{doc}/comments/{other['id']}/replies", json=body
        ).status_code
        == 409
    )

    async def leave():
        async with client.test_factory() as session:
            await session.execute(
                delete(TopicMembership).where(
                    TopicMembership.topic_id == uuid.UUID(room),
                    TopicMembership.member_handle == "thread-member",
                )
            )
            await session.commit()

    asyncio.run(leave())
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    assert client.post(prefix + "/replies", json=body).status_code == 403
    client.headers.update(owner_headers)
    assert read(client, doc, root)["revision"] == 2
    assert read(client, doc, other)["revision"] == 1
