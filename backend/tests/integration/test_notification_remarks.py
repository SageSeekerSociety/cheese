"""An inbox names people the way its one reader calls them.

A personal remark (``UserTeammateRemark``) is private to the viewer who set it.
A notification is read by exactly one recipient, so that recipient's own remark
replaces the public nickname in what they read — the same rule the roster
follows — while another person's request for the same row keeps the public name.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.notification.models import Notification, NotificationType
from tests.integration.conftest import UserCreator

MENTIONED_PUBLIC = "alice-remark-test"
ALICE_HANDLE = "bob-remark-test"
REMARK = "Alice from the lab"


async def _seed_mention(
    db_session, *, receiver_id: int, actor_id: int
) -> int:
    """One mention row naming the two people, stored the way the platform writes it."""
    now = datetime.now(UTC)
    row = Notification(
        receiver_id=receiver_id,
        type=NotificationType.MENTION,
        read=False,
        finalized=True,
        metadata_payload={
            "content": f"<@{ALICE_HANDLE}> asked <@{MENTIONED_PUBLIC}>",
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
    bystander.token = user_client.login(api_client, bystander.username, bystander.password)

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
    content = mine.json()["data"]["notification"]["contextMetadata"]["content"]
    assert REMARK in content, content

    theirs = api_client.get(
        f"/notifications/{bystander_view_id}",
        headers={"Authorization": f"Bearer {bystander.token}"},
    )
    assert theirs.status_code == 200, theirs.text
    stranger = theirs.json()["data"]["notification"]["contextMetadata"]["content"]
    assert REMARK not in stranger, stranger
    assert MENTIONED_PUBLIC in stranger, stranger
