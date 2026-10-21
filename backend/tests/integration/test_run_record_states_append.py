"""A later status preserves the earlier fact, including across a fresh reader."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.run_record.models import RunRecord
from app.domain.run_record.repository import RunRecordRepository


def test_later_status_does_not_rewrite_the_record(client):
    async def exercise():
        async with client.test_request_factory() as db:
            repository = RunRecordRepository(db)
            first = await repository.add(
                project_id=None,
                conversation_id=None,
                content="Waiting",
                meta={"event_type": "context_compact", "state": "running"},
            )
            first_id = first.id
            later = await repository.restate(
                first_id, content="Finished", meta={"state": "done"}
            )
            await db.flush()
            rows = list(
                await db.scalars(select(RunRecord).order_by(RunRecord.created_at))
            )
            assert [row.content for row in rows] == ["Waiting", "Finished"]
            assert later.id != first_id
            assert rows[0].meta["state"] == "running"

    client.portal.call(exercise)


def test_status_paging_keeps_every_record_sharing_a_timestamp(client):
    from tests.integration.conftest import post_project

    room = post_project(client, {"name": "Status paging"}, owner="user-1").json()[
        "data"
    ]["root_topic_id"]
    expected = {str(uuid.uuid4()) for _ in range(7)}

    async def seed():
        async with client.test_request_factory() as db:
            at = datetime.now(UTC)
            db.add_all(
                [
                    RunRecord(
                        id=uuid.UUID(identity),
                        conversation_id=uuid.UUID(room),
                        content=identity,
                        meta={"event_type": "context_compact"},
                        kind="context_compact",
                        severity="info",
                        created_at=at,
                    )
                    for identity in expected
                ]
            )
            await db.commit()

    client.portal.call(seed)
    seen = []
    before = None
    for _ in range(5):
        response = client.get(
            f"/topics/{room}/transcript",
            params={"limit": 2, **({"before": before} if before else {})},
        )
        assert response.status_code == 200, response.text
        page = response.json()["data"]
        seen.extend(row["id"] for row in page["data"])
        before = page["oldest_id"]
        if not page["has_more"]:
            break
    assert len(seen) == len(expected)
    assert set(seen) == expected
