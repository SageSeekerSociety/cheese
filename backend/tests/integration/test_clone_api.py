"""POST /topics/{id}/clone-from — transcript-fork clone (fusion-design §6).

Covers the sdk-backend degrade path and, with the tmux backend + host-mounted
session dirs faked into tmp, a real clone that forks the source transcript onto
the target topic.
"""

import asyncio
import uuid

from app.core.config import settings
from app.domain.agent import clone
from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.workspace import service as ws


def _project_and_topics(client) -> tuple[str, str, str]:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    src = client.post(
        "/topics", json={"project_id": p["id"], "title": "源话题"}
    ).json()["data"]
    dst = client.post(
        "/topics", json={"project_id": p["id"], "title": "目标话题"}
    ).json()["data"]
    return p["id"], src["id"], dst["id"]


def _seed_session(client, topic_id: str, session_id: str) -> None:
    async def _run() -> None:
        async with client.test_factory() as s:
            await AgentSessionRepository(s).save(
                topic_id=uuid.UUID(topic_id),
                agent_handle=CHEESE_HANDLE,
                resume_token=session_id,
            )
            await s.commit()

    asyncio.run(_run())


def _read_session(client, topic_id: str) -> str | None:
    async def _run() -> str | None:
        async with client.test_factory() as s:
            return await AgentSessionRepository(s).resume_token(
                uuid.UUID(topic_id), CHEESE_HANDLE
            )

    return asyncio.run(_run())


def test_clone_missing_source_id_is_422(client):
    _, _, dst = _project_and_topics(client)
    r = client.post(f"/topics/{dst}/clone-from", json={})
    assert r.status_code == 422


def test_clone_unsupported_on_sdk_backend(client, monkeypatch):
    """The default sdk backend has no session file → clean 422 (degrade)."""
    monkeypatch.setattr(settings, "agent_backend", "sdk")
    _, src, dst = _project_and_topics(client)
    _seed_session(client, src, "sess-src")
    r = client.post(f"/topics/{dst}/clone-from", json={"source_topic_id": src})
    assert r.status_code == 422
    assert "克隆" in r.json()["message"]


def test_clone_source_without_session_is_422(client, monkeypatch):
    monkeypatch.setattr(settings, "agent_backend", "tmux")
    _, src, dst = _project_and_topics(client)
    r = client.post(f"/topics/{dst}/clone-from", json={"source_topic_id": src})
    assert r.status_code == 422
    assert "还没跑过" in r.json()["message"]


def test_clone_forks_transcript_onto_target(client, monkeypatch, tmp_path):
    """tmux backend happy path: source transcript is copied + session-id-forked
    under the target's session mount, and the target is pointed at the new id."""
    monkeypatch.setattr(settings, "agent_backend", "tmux")

    # Fake ws.session_dir → a per-topic tmp dir (the host mount stand-in).
    def fake_session_dir(project_id, topic_id):
        d = tmp_path / topic_id.hex[:8]
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("app.domain.topic.services.ws.session_dir", fake_session_dir)

    _, src, dst = _project_and_topics(client)
    old_sid = "source-session-uuid"
    _seed_session(client, src, old_sid)

    # Lay down a source transcript where the (faked) mount would hold it — under
    # the legacy /work slug, as every pre-project-mount session wrote it.
    src_file = clone.transcript_file(
        fake_session_dir(None, uuid.UUID(src)),
        old_sid,
        cwd=clone.LEGACY_CONTAINER_CWD,
    )
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(f'{{"sessionId":"{old_sid}","text":"hi"}}', encoding="utf-8")

    r = client.post(f"/topics/{dst}/clone-from", json={"source_topic_id": src})
    assert r.status_code == 200
    # TopicOut doesn't surface session_id, so read the fork's new id from the DB.
    new_sid = _read_session(client, dst)
    assert new_sid and new_sid != old_sid

    # The forked transcript exists under the target topic's OWN workdir slug
    # (where its container's --resume will look), id rewritten.
    dst_dir = tmp_path / uuid.UUID(dst).hex[:8]
    dst_file = clone.transcript_file(
        dst_dir,
        new_sid,
        cwd=ws.sandbox_topic_workdir(uuid.UUID(dst)),
    )
    assert dst_file.is_file()
    body = dst_file.read_text(encoding="utf-8")
    assert old_sid not in body
    assert new_sid in body


def test_clone_cross_project_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "agent_backend", "tmux")
    p1 = client.post("/projects", json={"name": "P1"}).json()["data"]
    p2 = client.post("/projects", json={"name": "P2"}).json()["data"]
    src = client.post("/topics", json={"project_id": p1["id"], "title": "A"}).json()[
        "data"
    ]
    dst = client.post("/topics", json={"project_id": p2["id"], "title": "B"}).json()[
        "data"
    ]
    _seed_session(client, src["id"], "sess-x")
    r = client.post(
        f"/topics/{dst['id']}/clone-from",
        json={"source_topic_id": src["id"]},
    )
    assert r.status_code == 422
    assert "同一项目" in r.json()["message"]
