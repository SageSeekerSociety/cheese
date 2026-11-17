"""An inbox names people the way its one reader calls them.

A personal remark (``UserTeammateRemark``) is private to the viewer who set it.
A notification is read by exactly one recipient, so that recipient's own remark
replaces the public nickname in what they read — the same rule the roster
follows — while another person's request for the same row keeps the public name.
"""

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.notification.models import Notification, NotificationType
from tests.integration.conftest import UserCreator

MENTIONED_PUBLIC = "alice-remark-test"
ALICE_HANDLE = "bob-remark-test"
REMARK = "Alice from the lab"


async def _seed_mention(db_session, *, receiver_id: int, actor_id: int) -> int:
    """One mention row naming the two people, stored the way the platform writes it."""
    now = datetime.now(UTC)
    row = Notification(
        receiver_id=receiver_id,
        type=NotificationType.MENTION,
        read=False,
        finalized=True,
        metadata_payload={
            "content": f"<@{ALICE_HANDLE}> asked <@{MENTIONED_PUBLIC}>",
            # The written snapshot beside the handle (`authorName`): the page has
            # to relabel it too, or the title keeps the old name.
            "author": MENTIONED_PUBLIC,
            "authorName": MENTIONED_PUBLIC,
            "mentioner": {"type": "user", "id": str(actor_id)},
        },
        created_at=now,
        updated_at=now,
    )
    db_session.add(row)
    await db_session.commit()
    return row.id


def test_a_remark_renames_people_only_in_the_inbox_of_the_person_who_set_it(
    user_client: UserCreator, api_client: TestClient, db_session, _portal
):
    reader = user_client.create_user(nickname="reader")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    actor = user_client.create_user(username=ALICE_HANDLE, nickname=ALICE_HANDLE)
    user_client.create_user(username=MENTIONED_PUBLIC, nickname=MENTIONED_PUBLIC)
    bystander = user_client.create_user(nickname="bystander")
    bystander.token = user_client.login(
        api_client, bystander.username, bystander.password
    )

    async def seed() -> tuple[int, int]:
        return (
            await _seed_mention(
                db_session, receiver_id=reader.user_id, actor_id=actor.user_id
            ),
            await _seed_mention(
                db_session, receiver_id=bystander.user_id, actor_id=actor.user_id
            ),
        )

    reader_view_id, bystander_view_id = _portal.call(seed)

    saved = api_client.put(
        f"/users/{MENTIONED_PUBLIC}/remark",
        json={"remark": REMARK},
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert saved.status_code == 200, saved.text

    mine = api_client.get(
        f"/notifications/{reader_view_id}",
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert mine.status_code == 200, mine.text
    mine_meta = mine.json()["data"]["notification"]["contextMetadata"]
    assert REMARK in mine_meta["content"], mine_meta
    assert mine_meta["authorName"] == REMARK, mine_meta

    theirs = api_client.get(
        f"/notifications/{bystander_view_id}",
        headers={"Authorization": f"Bearer {bystander.token}"},
    )
    assert theirs.status_code == 200, theirs.text
    stranger_meta = theirs.json()["data"]["notification"]["contextMetadata"]
    assert REMARK not in stranger_meta["content"], stranger_meta
    assert MENTIONED_PUBLIC in stranger_meta["content"], stranger_meta
    assert stranger_meta["authorName"] == MENTIONED_PUBLIC, stranger_meta


def test_a_live_push_renames_people_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, db_session, _portal
):
    """The push a browser shows names people the way the inbox row does.

    A join request's push title is built from the ``requesterName`` snapshot
    written beside the ``requester`` handle, so that snapshot is what has to be
    relabelled — a token swap alone leaves the public name in the title.
    """
    from app.domain.notification.live import notices_after

    reader = user_client.create_user(nickname="live-reader")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    requester = "alice-live-test"
    requester_user = user_client.create_user(username=requester, nickname=requester)

    async def seed() -> int:
        row = Notification(
            receiver_id=reader.user_id,
            type=NotificationType.TEAM_JOIN_REQUEST,
            read=False,
            finalized=True,
            metadata_payload={
                # The shape the platform writes: an entity reference for who,
                # and the public name snapshotted beside it for the push.
                "requester": {"type": "user", "id": str(requester_user.user_id)},
                "requesterName": requester,
                "teamName": "一个团队",
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.commit()
        return row.id

    row_id = _portal.call(seed)

    saved = api_client.put(
        f"/users/{requester}/remark",
        json={"remark": REMARK},
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert saved.status_code == 200, saved.text

    async def read() -> dict:
        # ``after`` is the cursor the browser already showed; a push frame only
        # carries what came after it, so read from just under this row.
        return await notices_after(db_session, reader.user_id, row_id - 1)

    frame = _portal.call(read)
    said = " ".join(item["title"] + " " + item["body"] for item in frame["items"])
    assert REMARK in said, said


def test_a_reference_snapshot_is_swapped_by_the_persons_id_not_their_name(
    user_client: UserCreator, api_client: TestClient, db_session, _portal
):
    """Two people sharing a nickname must not take each other's remark.

    The snapshot beside a user reference is frozen at send time, so its string
    can equal another person's current public name. Keying the swap on the
    reference's id keeps the remark with the person the notification is about.
    """
    from app.domain.notification.live import notices_after

    reader = user_client.create_user(nickname="same-name-reader")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    # Two accounts with the same public nickname.
    sender = user_client.create_user(username="twins-a", nickname="Twin")
    other = user_client.create_user(username="twins-b", nickname="Twin")

    saved = api_client.put(
        f"/users/{sender.username}/remark",
        json={"remark": "The one I know"},
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert saved.status_code == 200, saved.text

    async def seed() -> int:
        row = Notification(
            receiver_id=reader.user_id,
            type=NotificationType.TEAM_JOIN_REQUEST,
            read=False,
            finalized=True,
            metadata_payload={
                "requester": {"type": "user", "id": str(other.user_id)},
                "requesterName": "Twin",
                "teamName": "一个团队",
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.commit()
        return row.id

    row_id = _portal.call(seed)

    async def read() -> dict:
        return await notices_after(db_session, reader.user_id, row_id - 1)

    frame = _portal.call(read)
    said = " ".join(item["title"] + " " + item["body"] for item in frame["items"])
    assert REMARK not in said, said
    assert "Twin" in said, said


def test_the_inbox_row_itself_renames_a_reference_only_person(
    user_client: UserCreator, api_client: TestClient, db_session, _portal
):
    """No ``<@handle>`` token anywhere: the reference's id alone must find the remark."""
    reader = user_client.create_user(nickname="ref-only-reader")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    requester = user_client.create_user(
        username="ref-only-alice", nickname="ref-only-alice"
    )

    saved = api_client.put(
        f"/users/{requester.username}/remark",
        json={"remark": REMARK},
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert saved.status_code == 200, saved.text

    async def seed() -> int:
        row = Notification(
            receiver_id=reader.user_id,
            type=NotificationType.TEAM_JOIN_REQUEST,
            read=False,
            finalized=True,
            recipient_handle=reader.username,
            metadata_payload={
                "requester": {"type": "user", "id": str(requester.user_id)},
                "requesterName": requester.username,
                "teamName": "一个团队",
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.commit()
        return row.id

    row_id = _portal.call(seed)

    r = api_client.get(
        f"/notifications/{row_id}", headers={"Authorization": f"Bearer {reader.token}"}
    )
    assert r.status_code == 200, r.text
    meta = r.json()["data"]["notification"]["contextMetadata"]
    who = (
        meta.get("message", {}).get("params", {}).get("requester")
        if isinstance(meta.get("message"), dict)
        else None
    )
    assert REMARK in json.dumps(meta, ensure_ascii=False), meta
    assert who in (None, REMARK), who


def test_a_page_that_supplies_referents_still_renames_their_snapshots(
    user_client, api_client, db_session, _portal
):
    """The page's id->handle map must reach ``names``, or the snapshot stays public."""
    from app.domain.notification.models import Notification as _N
    from app.domain.notification.services import NotificationQueryService

    reader = user_client.create_user(nickname="page-ref-reader")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    requester = user_client.create_user(
        username="page-ref-alice", nickname="page-ref-alice"
    )

    saved = api_client.put(
        f"/users/{requester.username}/remark",
        json={"remark": REMARK},
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert saved.status_code == 200, saved.text

    async def read() -> dict:
        row = _N(
            receiver_id=reader.user_id,
            type=NotificationType.TEAM_JOIN_REQUEST,
            read=False,
            finalized=True,
            recipient_handle=reader.username,
            metadata_payload={
                "requester": {"type": "user", "id": str(requester.user_id)},
                "requesterName": requester.username,
                "teamName": "一个团队",
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.commit()
        service = NotificationQueryService.from_session(db_session)
        # Exactly what the list endpoint hands in: names skipped, referents known.
        dto = await service.build_notification_dto(
            row,
            names=None,
            referents={requester.user_id: requester.username},
            viewer_id=reader.user_id,
        )
        return dto.contextMetadata

    meta = _portal.call(read)
    assert REMARK in json.dumps(meta, ensure_ascii=False), meta
