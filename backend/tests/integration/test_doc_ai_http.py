"""New routes enforce real human and room authorization even in permissive dev."""

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.doc_ai.schemas import CompletionUsage, ProposalResult
from app.domain.doc_ai.services import DocAiService
from app.domain.living_doc.services import content_hash
from tests.conftest import seed_user
from tests.integration.conftest import (
    room_agent_seat,
    session_auth_headers,
    session_token,
)
from tests.integration.test_docs import _topic


def setup(client):
    room = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    saved = client.put(
        f"/topics/{room}/doc", json={"content": "😀原文\r\n", "expected_version": 0}
    )
    assert saved.status_code == 200
    doc = saved.json()["data"]
    body = {
        "operation_id": str(uuid.uuid4()),
        "kind": "ask",
        "question": "解释",
        "document_id": doc["id"],
        "base_version": doc["doc_version"],
    }
    return room, body


def test_request_creation_replay_payload_conflict_and_cancel_preserve_canonical(client):
    room, body = setup(client)
    before = client.get(f"/topics/{room}/doc").json()
    source = client.get(f"/topics/{room}/doc-ai/source")
    assert source.status_code == 200
    raw = source.json()["data"]
    assert raw["offset_unit"] == "utf8-bytes"
    assert raw["source"] == "😀原文\r\n" and raw["base_version"] == 1
    assert raw["nodes"][0]["start"] == 0
    assert raw["nodes"][0]["end"] == len("😀原文".encode())
    response = client.post(f"/topics/{room}/doc-ai/requests", json=body)
    assert response.status_code == 202, response.text
    assert (
        client.post(f"/topics/{room}/doc-ai/requests", json=body).json()
        == response.json()
    )
    mismatch = client.post(
        f"/topics/{room}/doc-ai/requests", json={**body, "question": "另一问"}
    )
    assert mismatch.status_code == 409
    request_id = response.json()["data"]["request_id"]
    state = client.get(f"/topics/{room}/doc-ai/requests/{request_id}")
    assert state.status_code == 200 and state.json()["data"]["state"] == "pending"
    assert (
        client.post(f"/topics/{room}/doc-ai/requests/{request_id}/cancel").json()[
            "data"
        ]["state"]
        == "cancelled"
    )
    assert client.get(f"/topics/{room}/doc").json() == before


def test_request_details_keep_frozen_context_after_canonical_changes(client):
    room, body = setup(client)
    created = client.post(f"/topics/{room}/doc-ai/requests", json=body)
    request_id = created.json()["data"]["request_id"]
    url = f"/topics/{room}/doc-ai/requests/{request_id}"
    frozen = {
        "question": "解释",
        "document_id": body["document_id"],
        "base_version": 1,
        "source": "😀原文\r\n",
        "source_hash": content_hash("😀原文\r\n"),
        "selection": None,
        "offset_unit": "utf8-bytes",
    }
    assert client.get(url).json()["data"]["frozen_context"] == frozen
    changed = client.put(
        f"/topics/{room}/doc", json={"content": "后来改过的正文", "expected_version": 1}
    )
    assert changed.status_code == 200
    before = client.get(f"/topics/{room}/doc").json()
    assert client.get(url).json()["data"]["frozen_context"] == frozen
    assert client.get(f"/topics/{room}/doc").json() == before
    assert (
        len(client.get(f"/topics/{room}/doc-ai/requests").json()["data"]["requests"])
        == 1
    )
    other_room, _ = setup(client)
    assert (
        client.get(f"/topics/{other_room}/doc-ai/requests/{request_id}").status_code
        == 404
    )
    seed_user(client, "frozen-context-outsider")
    client.headers.update(session_auth_headers("frozen-context-outsider"))
    denied = client.get(url)
    assert denied.status_code == 403
    assert "frozen_context" not in denied.json()


def test_frozen_context_is_not_exposed_to_another_actor_in_the_same_room(client):
    room, body = setup(client)
    created = client.post(f"/topics/{room}/doc-ai/requests", json=body)
    request_id = created.json()["data"]["request_id"]

    async def belong_to_other_actor():
        async with client.test_factory() as session:
            row = await DocAiService(session).get(
                uuid.UUID(room), uuid.UUID(request_id)
            )
            row.actor = "another-human-request-owner"
            await session.commit()

    asyncio.run(belong_to_other_actor())
    denied = client.get(f"/topics/{room}/doc-ai/requests/{request_id}")
    assert denied.status_code == 403
    assert "frozen_context" not in denied.json()


@pytest.mark.parametrize("changed_payload", [False, True])
def test_concurrent_http_request_claim_has_one_persisted_request(
    client, changed_payload
):
    room, body = setup(client)
    before = client.get(f"/topics/{room}/doc").json()
    start = threading.Barrier(2)

    def submit(question):
        start.wait(timeout=10)
        return client.post(
            f"/topics/{room}/doc-ai/requests", json={**body, "question": question}
        )

    questions = [body["question"], "另一问" if changed_payload else body["question"]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, questions))
    assert sorted(response.status_code for response in responses) == (
        [202, 409] if changed_payload else [202, 202]
    ), [response.text for response in responses]
    winner = next(response for response in responses if response.status_code == 202)
    if not changed_payload:
        assert responses[0].json() == responses[1].json()
    listed = client.get(f"/topics/{room}/doc-ai/requests").json()["data"]["requests"]
    assert len(listed) == 1
    assert listed[0]["request_id"] == winner.json()["data"]["request_id"]
    assert client.get(f"/topics/{room}/doc").json() == before

    async def persisted():
        from sqlalchemy import select

        from app.domain.doc_ai.models import DocAiAttempt, DocAiRequest
        from app.domain.living_doc.models import DocumentOperation

        async with client.test_factory() as session:
            requests = list(
                await session.scalars(
                    select(DocAiRequest).where(DocAiRequest.room_id == uuid.UUID(room))
                )
            )
            operations = list(
                await session.scalars(
                    select(DocumentOperation).where(
                        DocumentOperation.room_id == uuid.UUID(room),
                        DocumentOperation.action == "ai-request",
                    )
                )
            )
            assert len(requests) == len(operations) == 1
            assert operations[0].receipt == winner.json()
            assert requests[0].question == questions[responses.index(winner)]
            assert (
                await session.scalar(
                    select(DocAiAttempt.id).where(
                        DocAiAttempt.request_id == requests[0].id
                    )
                )
                is None
            )

    asyncio.run(persisted())


def test_real_human_accept_stored_proposal_only_and_replays_after_lost_response(client):
    room, body = setup(client)
    tree = client.get(f"/topics/{room}/docs")
    assert tree.status_code == 200, tree.text
    node_id = tree.json()["data"]["data"][0]["id"]
    body.update(
        kind="propose",
        selection={
            "node_id": node_id,
            "start": 0,
            "end": len("😀原文".encode()),
            "exact_hash": content_hash("😀原文"),
        },
    )
    created = client.post(f"/topics/{room}/doc-ai/requests", json=body)
    assert created.status_code == 202, created.text

    async def finish():
        async with client.test_factory() as session:
            lease = await DocAiService(session).claim_next()
            await session.commit()
        async with client.test_factory() as session:
            await DocAiService(session).settle(
                lease,
                result=ProposalResult(answer="只改所选", replacement="新😀原文"),
                usage=CompletionUsage(
                    model=lease.binding["wire_model"],
                    input_tokens=5,
                    output_tokens=5,
                    cost_usd=0.01,
                    upstream_id="http-proof",
                ),
            )
            await session.commit()

    asyncio.run(finish())
    request_id = created.json()["data"]["request_id"]
    proposal_id = client.get(f"/topics/{room}/doc-ai/requests/{request_id}").json()[
        "data"
    ]["proposal_id"]
    assert proposal_id
    detail_url = f"/topics/{room}/doc-ai/requests/{request_id}"
    frozen = client.get(detail_url).json()["data"]["frozen_context"]
    assert frozen["selection"] == body["selection"]
    assert frozen["question"] == body["question"]
    assert frozen["source"] == "😀原文\r\n"
    assert frozen["source_hash"] == content_hash("😀原文\r\n")
    url = f"/topics/{room}/doc-ai/proposals/{proposal_id}/accept"
    accept = {"operation_id": str(uuid.uuid4()), "expected_version": 1, "revision": 1}
    assert (
        client.post(url, json={**accept, "replacement": "恶意覆盖"}).status_code == 400
    )
    agent = room_agent_seat(client, room)
    client.headers.update(session_auth_headers(agent))
    denied = client.post(url, json=accept)
    assert denied.status_code == 403, denied.text
    client.headers.update(session_auth_headers("owner"))
    pending = client.get(f"/topics/{room}/doc-ai/proposals/{proposal_id}")
    assert pending.json()["data"]["state"] == "pending"
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 1
    saved = client.post(url, json=accept)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["content"] == "新😀原文\r\n"
    assert client.post(url, json=accept).json() == saved.json()
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 2
    assert client.get(detail_url).json()["data"]["frozen_context"] == frozen


def test_strict_schema_rejects_author_target_and_coerced_versions(client):
    room, body = setup(client)
    for extra in [
        {"author": "owner"},
        {"tools": ["cheese_doc_set"]},
        {"base_version": "1"},
        {"base_version": True},
    ]:
        response = client.post(
            f"/topics/{room}/doc-ai/requests", json={**body, **extra}
        )
        assert response.status_code == 400, response.text
    assert (
        client.post(
            f"/topics/{room}/doc-ai/requests", json={**body, "kind": "propose"}
        ).status_code
        == 400
    )
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 1


def test_no_global_or_anonymous_human_authority_even_with_authz_disabled(
    client, monkeypatch
):
    room, body = setup(client)
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    client.headers.pop("Authorization", None)
    assert client.post(f"/topics/{room}/doc-ai/requests", json=body).status_code == 401
    accept = {"operation_id": str(uuid.uuid4()), "expected_version": 1, "revision": 1}
    assert (
        client.post(
            f"/topics/{room}/doc-ai/proposals/{uuid.uuid4()}/accept", json=accept
        ).status_code
        == 401
    )
    seed_user(client, "outsider")
    client.headers.update(session_auth_headers("outsider"))
    assert client.post(f"/topics/{room}/doc-ai/requests", json=body).status_code == 403
    assert (
        client.post(
            f"/topics/{room}/doc-ai/proposals/{uuid.uuid4()}/accept", json=accept
        ).status_code
        == 403
    )


def test_agent_bearer_scoped_invalid_expired_and_wrong_room_cannot_accept(
    client, monkeypatch
):
    room, body = setup(client)
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    agent = room_agent_seat(client, room)
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    accept = {"operation_id": str(uuid.uuid4()), "expected_version": 1, "revision": 1}
    url = f"/topics/{room}/doc-ai/proposals/{uuid.uuid4()}/accept"
    client.headers.update(session_auth_headers(agent))
    assert client.post(url, json=accept).status_code == 403
    client.headers.pop("Authorization", None)
    scoped = mint_scoped_token(project_id=project, topic_id=room, agent_handle=agent)
    assert (
        client.post(url, json=accept, headers={"X-Cheese-Token": scoped}).status_code
        == 401
    )
    wrong = mint_scoped_token(
        project_id=project, topic_id=str(uuid.uuid4()), agent_handle=agent
    )
    assert (
        client.post(url, json=accept, headers={"X-Cheese-Token": wrong}).status_code
        == 403
    )
    for token in ["invalid", session_token("owner", ttl_s=-1)]:
        assert (
            client.post(
                url, json=accept, headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
    client.headers.update(session_auth_headers("owner"))
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 1


def test_raw_selection_cannot_cross_blocks_or_point_at_wrong_node(client):
    room, body = setup(client)
    # No quote fallback: a random node cannot authorize even a correct raw hash.
    body.update(
        kind="propose",
        selection={
            "node_id": str(uuid.uuid4()),
            "start": 0,
            "end": len("😀原文".encode()),
            "exact_hash": content_hash("😀原文"),
        },
    )
    assert client.post(f"/topics/{room}/doc-ai/requests", json=body).status_code == 422
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 1
