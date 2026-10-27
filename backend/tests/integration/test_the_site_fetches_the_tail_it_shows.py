"""现场 reads a page without fetching the whole turn, and fetches it in parallel.

The rules, without the code: a page shows the newest records, so opening 现场
must not pull the beginning of a long turn; a sealed object never changes, so
reading the same page again must not fetch it again; and a page that spans
several turns must fetch them at the same time rather than one after another.
All three are what a reader waits for.
"""

import asyncio
import gzip
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.session_record import models, service
from app.domain.session_record.models import SessionRecord
from tests.integration.test_a_turns_raw_record_keeps_what_it_did import (
    _Bucket,
    _room_for,
    _seal,
    _Session,
)


def _drop_rows(client, topic: uuid.UUID) -> None:
    """Take the conversation's rows away, as the retention sweep does at thirty
    days: the objects stay in the bucket and the index still names them.

    Without this the database's own copy answers every read and a paging test
    passes whatever the archive walk does — the object path is never taken. A
    sealed run's row and its object hold the same records, so a test that means
    to exercise the walk has to leave the walk with only one of them.
    """

    async def run() -> None:
        async with client.test_request_factory() as db:
            await db.execute(
                delete(SessionRecord).where(SessionRecord.conversation_id == topic)
            )
            await db.commit()

    client.portal.call(run)


def _drop_rows_for_turn(client, topic: uuid.UUID, turn: str) -> None:
    """Take **one turn's** rows away, as the retention sweep does at thirty days.

    The rest of the conversation keeps its rows, so the page the reader gets is
    built from what the database still holds — and the turn dropped here is
    reachable only through the index. `turn` is the work id the journal stamped
    on that turn's records; the row carries it as the turn id."""

    async def run() -> None:
        async with client.test_request_factory() as db:
            await db.execute(
                delete(SessionRecord).where(
                    SessionRecord.conversation_id == topic,
                    SessionRecord.turn_id == uuid.UUID(turn),
                )
            )
            await db.commit()

    client.portal.call(run)


def _long_turn(work: str, count: int) -> list[dict]:
    """One turn's journal: `count` steps, each naming itself."""
    return [
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "message": {
                "id": f"msg_{i}",
                "role": "assistant",
                "content": [{"type": "text", "text": f"step {i:04d}"}],
            },
            "parent_tool_use_id": None,
            "cheese": {"work_id": work},
        }
        for i in range(count)
    ]


class _CountingBucket(_Bucket):
    """The private bucket, in memory, keeping the bodies it was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[bytes] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.listings = 0

    async def list_keys(self, prefix: str):
        self.listings += 1
        async for key in super().list_keys(prefix):
            yield key

    async def download(self, key: str) -> bytes | None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Let the other fetches of the same page start before this one ends:
            # a caller that fetches them one by one never reaches two.
            await asyncio.sleep(0.02)
            body = self.objects.get(key)
            if body is not None:
                self.bodies.append(body)
            return body
        finally:
            self.in_flight -= 1


def _site(
    client,
    topic: uuid.UUID,
    *,
    limit: int = 120,
    before: str | None = None,
    after: str | None = None,
) -> dict:
    params = {
        "limit": limit,
        **({"before": before} if before else {}),
        **({"after": after} if after else {}),
    }
    response = client.get(f"/topics/{topic}/transcript", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _sealed(
    client, tmp_path, monkeypatch, count: int
) -> tuple[_CountingBucket, uuid.UUID]:
    bucket = _CountingBucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    monkeypatch.setattr(service, "bucket", lambda: bucket)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    session.write(_long_turn(str(uuid.uuid4()), count))
    # One drain takes one journal page (256 records); a turn longer than that
    # needs as many passes as it has pages.
    session.drain(pages=(count + 255) // 256)
    _seal(client, bucket)
    return bucket, session.ref.topic_id


def test_the_newest_page_does_not_fetch_the_beginning_of_a_long_turn(
    client, tmp_path, monkeypatch
):
    # A long turn: one turn, several sealed runs. The number is the test's own
    # (it does not read the seal cap), so changing the cap does not change what
    # the reader asked for — only how much of it a page has to fetch.
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=1200)
    bucket.bodies.clear()

    page = _site(client, topic)

    assert len(page["data"]) == 120
    bodies = [gzip.decompress(body).decode() for body in bucket.bodies]
    fetched = "".join(bodies)
    assert "step 1199" in fetched, "the page's own steps"
    # The page is the tail of the turn; its first steps are not on screen and
    # must not be fetched (gzip cannot be read from the middle).
    assert '"step 0000"' not in fetched


def test_a_page_the_rows_already_hold_never_lists_the_index(
    client, tmp_path, monkeypatch
):
    # Sealed runs keep their rows until the retention sweep, so the newest page
    # is answerable from the database alone. Listing the bucket costs a round
    # trip the open panel pays every three seconds; it must not be spent when
    # the rows have already filled the page.
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=600)
    bucket.listings = 0

    page = _site(client, topic)

    assert len(page["data"]) == 120
    assert bucket.listings == 0, "读者要的那一页数据库自己就有"


def test_a_forward_read_inside_the_window_never_lists_the_index(
    client, tmp_path, monkeypatch
):
    """现场 three-second refresh 不走归档索引。

    面板开着时每 3 秒读一次增量（`after` 游标，几秒前）。那个游标落在保留窗口
    以内，而索引只能说出窗口内每一个对象——它们每一条都还有行（行是最后才被清理
    的），所以列一遍索引除了重复行里已有的东西，一条也添不上。限流就是在这里发生
    的：3 秒一次、一次不落地列，撞上对象存储的并发上限，读慢到超时。
    """
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=600)
    newest = _site(client, topic, limit=50)["data"][-1]["meta"]["cursor"]
    bucket.listings = 0

    _site(client, topic, limit=50, after=newest)

    assert bucket.listings == 0, "游标还在保留窗口里，索引说不出这一页缺的任何一条"


def test_a_forward_read_older_than_the_window_still_lists_the_index(
    client, tmp_path, monkeypatch
):
    """游标翻到三十天以前时仍要列索引——这正是它存在的理由。

    过了保留期，更早那几个封存块的行已经被清理，只有索引还知道它们；这时跳过
    列索引就等于把读者和更早的记录之间那一段整个抹掉。
    """
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=600)
    _drop_rows(client, topic)
    stale = service.encode_cursor(
        (
            datetime.now(UTC) - models.RETENTION - timedelta(days=1),
            "claude_code",
            "cheese",
            0,
        )
    )
    bucket.listings = 0

    page = _site(client, topic, limit=50, after=stale)

    assert len(page["data"]) > 0, "更早的记录还在归档里，读者得读得到"
    assert bucket.listings > 0, "窗口之外，索引是唯一能说出那些封存块的地方"


def test_a_page_the_rows_exactly_fill_still_reaches_the_archive(
    client, tmp_path, monkeypatch
):
    """行正好填满一页时，更早的记录还在归档里，读者得能翻上去。

    这一条守的是那个等号：只有行给出**多于**一页时才有一道边界可交回去、才
    谈得上不必列归档。正好一页时，撑起「上面还有」的那几条可能正是行已经被清理
    掉的记录（三十天保留），省掉列归档就等于告诉读者上面没有了。"""
    bucket = _CountingBucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    monkeypatch.setattr(service, "bucket", lambda: bucket)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    # 更早的一轮：稍后把它的行清掉，只留归档里的对象与索引。
    older = str(uuid.uuid4())
    session.write(_long_turn(older, 50))
    session.drain()
    # 更新的一轮：行数正好一页（默认 120）。
    session.write(_long_turn(str(uuid.uuid4()), 120))
    session.drain()
    _seal(client, bucket)
    topic = session.ref.topic_id
    _drop_rows_for_turn(client, topic, older)

    page = _site(client, topic)

    assert len(page["data"]) == 120
    assert page["has_more"] is True, "上面还有更早的一轮，读者得翻得到"
    assert page["oldest_id"], "交回去一个能往上翻的游标"


def test_reading_the_same_page_again_keeps_the_sealed_body(
    client, tmp_path, monkeypatch
):
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=900)
    bucket.bodies.clear()

    _site(client, topic)
    first = len(bucket.bodies)
    assert first > 0

    _site(client, topic)
    assert len(bucket.bodies) == first, "a sealed object is immutable; it was refetched"


def test_a_page_spanning_several_turns_fetches_them_together(
    client, tmp_path, monkeypatch
):
    bucket = _CountingBucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    monkeypatch.setattr(service, "bucket", lambda: bucket)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    for _ in range(3):
        session.write(_long_turn(str(uuid.uuid4()), 60))
    session.drain()
    _seal(client, bucket)
    bucket.max_in_flight = 0

    page = _site(client, session.ref.topic_id)

    assert len(page["data"]) == 120
    assert bucket.max_in_flight > 1, "each turn's body was fetched after the last one"


def test_paging_up_by_cursor_walks_every_record_once(client, tmp_path, monkeypatch):
    """A reader paging up crosses sealed objects: every record once, in order.

    Pages here are far smaller than a sealed object, so the walk crosses
    several of them — the paging the newest-page tests never exercise, and the
    one the stop-instead-of-overread change could break with an off-by-one.
    The rows are taken away first: with the database holding a copy of every
    sealed run, a read is answered from there and the archive walk under test
    is never taken.
    """
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=900)
    _drop_rows(client, topic)
    bucket.bodies.clear()

    # 现场打开在最新一段，往上翻：一页比这里读到的更早，所以拼在已读的前面。
    steps: list[str] = []
    before = None
    while True:
        page = _site(client, topic, limit=50, before=before)
        steps = [
            block["meta"]["parts"][0]["text"]
            for block in page["data"]
            if block.get("meta", {}).get("parts")
        ] + steps
        if not page["has_more"]:
            break
        assert page["oldest_id"], "a page that has more must name where to go on"
        before = page["oldest_id"]

    assert steps == [f"step {i:04d}" for i in range(900)]


def test_a_page_the_newest_object_fills_fetches_no_more(client, tmp_path, monkeypatch):
    """The newest object fills the page, so the walk stops at it.

    A read must not fetch what it will not show: a turn long enough to seal
    into two objects, read at a page smaller than one, must fetch one. A batch
    that reaches past the page reddens this.
    """
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=700)
    bucket.bodies.clear()

    page = _site(client, topic, limit=50)

    steps = [
        block["meta"]["parts"][0]["text"]
        for block in page["data"]
        if block.get("meta", {}).get("parts")
    ]
    assert steps == [f"step {i:04d}" for i in range(650, 700)]
    assert len(bucket.bodies) == 1, "the page came from one object; more were fetched"
    assert (
        json.loads(gzip.decompress(bucket.bodies[0]).decode().splitlines()[0])[
            "record"
        ]["message"]["content"][0]["text"]
        == "step 0512"
    )
