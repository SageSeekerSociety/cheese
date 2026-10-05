"""项目那两条读接口的线形状：/weeklies、/tasks。

钉的是**同一个用户看得见的东西**，一条一条对到线上，而不是「函数被调过」：

* `/weeklies` 是项目的原话，每一行的字段、顺序、`meta` 都是契约。
* `/tasks` 每一行带 `presentation`（哪一列、卡面那句话）和 `card`（这条活此刻骑的
  那张卡，窄到侧栏画得出来的四个字段）。

这两条以前各读一次别的领域的 repository，现在各走对方领域的一个窄读入口（#2143）。
窄读入口把行折成**纯值**再交出来 —— 所以这里的负样本卡把 `note_code`、
`merge_state`、`decided_by`/`auto_merge_armed_by` 都填上，钉住「显示状态是从这些
值算出来的」：谁把那一位从窄读里丢掉，哪一行的说法就会变，而用户看得见的就是那句
话。只数「字段个数没少」是钉不住的。
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.room_task.models import Task
from app.domain.room_task.presentation import Building, Delivering, NeedsYou
from tests.integration.conftest import post_project

# 两个时刻，只为了把「最新在前」这条顺序钉死 —— 同一次请求里的两条，靠 created_at
# 分开，而不是靠它们在库里碰巧的落点。
OLDER = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
NEWER = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _wire(moment: datetime) -> str:
    """A datetime column as it reaches the wire: pydantic writes UTC as `Z`."""
    return moment.isoformat().replace("+00:00", "Z")


def _project(client) -> str:
    r = post_project(client, json={"name": "P"}, owner="alice")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "房间") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seed(client, build) -> None:
    """在 client 的测试库里跑一段 async 的种子代码，和请求共用同一个库。"""

    async def _run() -> None:
        async with client.test_factory() as s:
            await build(s)
            await s.commit()

    asyncio.run(_run())


def _list(client, project_id: str, tail: str) -> dict:
    r = client.get(f"/projects/{project_id}/{tail}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _rows(client, project_id: str, tail: str) -> list[dict]:
    return _list(client, project_id, tail)["data"]


# —— 周报集 /weeklies ———————————————————————————————————————————


def test_weeklies_come_back_newest_first_and_carry_their_window(client):
    project = _project(client)
    room = _room(client, project)

    ids: dict[str, str] = {}

    async def _seed_them(s):
        older = Block(
            project_id=uuid.UUID(project),
            conversation_id=uuid.UUID(room),
            kind=BlockKind.weekly,
            author_type=AuthorType.participant,
            author="alice",
            content="上一周：搭好了骨架。",
            refs=[room],
            meta={"since": OLDER.isoformat(), "until": NEWER.isoformat()},
            created_at=OLDER,
        )
        newer = Block(
            project_id=uuid.UUID(project),
            conversation_id=uuid.UUID(room),
            kind=BlockKind.weekly,
            author_type=AuthorType.participant,
            author="alice",
            content="这一周：产物页上线。",
            refs=[room],
            meta={
                "since": NEWER.isoformat(),
                "until": datetime(2026, 9, 9, 10, 0, tzinfo=UTC).isoformat(),
            },
            created_at=NEWER,
        )
        s.add_all([older, newer])
        await s.flush()
        ids["older"], ids["newer"] = str(older.id), str(newer.id)

    _seed(client, _seed_them)

    payload = _list(client, project, "weeklies")
    assert payload["total"] == 2
    assert [row["id"] for row in payload["data"]] == [ids["newer"], ids["older"]]

    # 一行整份对下来，不是挑几个键。BlockOut 是这条线对外的契约；窗口是这一行
    # 的身份：并排摆着的几份周报，是它把它们分开的。
    newest = payload["data"][0]
    assert newest == {
        "id": ids["newer"],
        "conversation_id": room,
        "kind": "weekly",
        "author_type": "participant",
        "author": "alice",
        "content": "这一周：产物页上线。",
        "reply_to": None,
        "mime_type": None,
        "refs": [room],
        "upgraded_to_topic_id": None,
        "upgraded_to_task_id": None,
        "turn_id": None,
        "meta": {
            "since": NEWER.isoformat(),
            "until": "2026-09-09T10:00:00+00:00",
        },
        "reactions": [],
        "created_at": _wire(NEWER),
    }


def test_a_project_with_no_weeklies_says_empty_rather_than_guessing(client):
    project = _project(client)
    assert _list(client, project, "weeklies") == {"data": [], "total": 0}


# —— 活 /tasks —————————————————————————————————————————————————


def test_a_project_with_no_threads_says_empty_rather_than_guessing(client):
    project = _project(client)
    assert _list(client, project, "tasks") == {"data": [], "total": 0}


def test_every_thread_comes_back_oldest_first_with_its_board_cell_and_card(client):
    project = _project(client)
    room = _room(client, project)

    ids: dict[str, str] = {}

    async def _seed_them(s):
        first = Task(
            project_id=uuid.UUID(project),
            room_id=uuid.UUID(room),
            title="第一件",
            created_at=OLDER,
            started_at=OLDER,
        )
        second = Task(
            project_id=uuid.UUID(project),
            room_id=uuid.UUID(room),
            title="第二件",
            created_at=NEWER,
            started_at=NEWER,
        )
        s.add_all([first, second])
        await s.flush()
        s.add(
            AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=second.id,
                reviewer_handle="alice",
                status=AcceptStatus.pending,
                pr_number=1789,
                pr_url="https://github.com/acme/web/pull/1789",
            )
        )
        ids["first"], ids["second"] = str(first.id), str(second.id)

    _seed(client, _seed_them)

    payload = _list(client, project, "tasks")
    assert payload["total"] == 2
    # 旧的在前面：这是树画出来的顺序，也是 /topics/{id}/tasks 用的同一份顺序。
    assert [row["id"] for row in payload["data"]] == [ids["first"], ids["second"]]

    no_card, with_card = payload["data"]
    # 大多数活在做的过程中都没有卡。空就是空，不编一个状态出来。
    assert no_card["card"] is None
    assert no_card["title"] == "第一件"
    assert no_card["project_id"] == project
    assert no_card["room_id"] == room
    assert no_card["presentation"] == {
        "column": "building",
        "phrase": Building.started,
    }

    # 有卡的那一行：`card` 窄到侧栏画得出来的四个字段，一个不多一个不少。
    assert with_card["title"] == "第二件"
    assert set(with_card["card"]) == {"id", "status", "pr_number", "pr_url"}
    assert with_card["card"]["status"] == "pending"
    assert with_card["card"]["pr_number"] == 1789
    assert with_card["card"]["pr_url"] == "https://github.com/acme/web/pull/1789"
    assert with_card["card"]["id"]
    # 一张什么都没镜像、没入队、没人采纳的卡，就是在等你去审阅。
    assert with_card["presentation"] == {
        "column": "needs_you",
        "phrase": NeedsYou.awaiting_review,
    }


# —— 负样本：卡上那几位真的被读到了吗 ——————————————————————————————
#
# 每一张卡只填一位关键的值，其余留空，然后把**用户在屏幕上看到的那句话**对下来。
# 谁把那一位从窄读里丢掉，哪一行的说法就变 —— 只数字段个数是钉不住这个的，因为
# 丢一位字段个数就少了，但如果实现改成「读另一个不相关的字段」，个数不变、说法却
# 错了。这里钉的是说法。


def _carded_tasks(client, project: str, room: str, cards: list[dict]) -> dict[str, str]:
    """在同一个房间上种几条活，各挂一张卡；卡的内容由 `cards` 逐条给。"""
    ids: dict[str, str] = {}

    async def _seed_them(s):
        for i, spec in enumerate(cards):
            task = Task(
                project_id=uuid.UUID(project),
                room_id=uuid.UUID(room),
                title=f"活 {i}",
                created_at=OLDER,
                started_at=OLDER,
            )
            s.add(task)
            await s.flush()
            s.add(
                AcceptCard(
                    topic_id=uuid.UUID(room),
                    task_id=task.id,
                    reviewer_handle="alice",
                    **spec,
                )
            )
            ids[f"t{i}"] = str(task.id)

    _seed(client, _seed_them)
    return ids


def test_note_code_decides_the_cell_even_when_the_merge_mirror_says_something_else(
    client,
):
    """`note_code` 压过合并态镜像。

    平台入队时亲手记下「已进合并队列」，此后 GitHub 对它的合并态报的是 unknown
    或旧的 dirty；note 是平台写下的入队凭据，比轮询读到的镜像硬（#2046）。这一格
    要是掉了 `note_code`，同一张卡会被读成「解决冲突」—— 而队里在跑检查，谁都不用
    去解冲突。
    """
    project = _project(client)
    room = _room(client, project)
    ids = _carded_tasks(
        client,
        project,
        room,
        [
            {
                "status": AcceptStatus.pending,
                "note_code": NoteCode.waiting_merge_queue,
                "merge_state": {"state": "dirty", "who": "agent"},
            }
        ],
    )

    (row,) = _rows(client, project, "tasks")
    assert row["id"] == ids["t0"]
    assert row["presentation"] == {
        "column": "delivering",
        "phrase": Delivering.awaiting_checks,
    }


def test_the_merge_mirror_decides_that_the_platform_is_driving(client):
    """合并态镜像里的 state/who 决定这一步在谁手上。

    BEHIND（strict）是平台自己在 update-branch —— 下一步不在人手上，别去催人。
    掉了 `merge_state`，同一张卡会掉进默认的「等你审阅」。
    """
    project = _project(client)
    room = _room(client, project)
    _carded_tasks(
        client,
        project,
        room,
        [
            {
                "status": AcceptStatus.pending,
                "merge_state": {"state": "behind", "who": "platform"},
            }
        ],
    )

    (row,) = _rows(client, project, "tasks")
    assert row["presentation"] == {
        "column": "delivering",
        "phrase": Delivering.updating_branch,
    }


def test_a_decided_card_is_not_reported_as_waiting_for_review(client):
    """人已经采纳过了：GitHub 还没算完合并态，但这一步早就不在等人。

    `decided` 是从 `decided_by` 算出来的（#2062）。掉了它，同一张卡会被说成
    「等你审阅」—— 去催一个刚点过采纳的人（#2046）。
    """
    project = _project(client)
    room = _room(client, project)
    _carded_tasks(
        client,
        project,
        room,
        [
            {
                "status": AcceptStatus.pending,
                "merge_state": {"state": "unknown", "who": "platform"},
                "decided_by": "alice",
            }
        ],
    )

    (row,) = _rows(client, project, "tasks")
    assert row["presentation"] == {
        "column": "delivering",
        "phrase": Delivering.awaiting_checks,
    }


def test_an_armed_auto_merge_counts_as_decided_too(client):
    """布防「绿了自动合」也是把这一步交出去了，判据和 `decided_by` 同一条。"""
    project = _project(client)
    room = _room(client, project)
    _carded_tasks(
        client,
        project,
        room,
        [
            {
                "status": AcceptStatus.pending,
                "merge_state": {"state": "unknown", "who": "platform"},
                "auto_merge_armed_by": "alice",
            }
        ],
    )

    (row,) = _rows(client, project, "tasks")
    assert row["presentation"] == {
        "column": "delivering",
        "phrase": Delivering.awaiting_checks,
    }


def test_an_unmirrored_pending_card_really_is_waiting_for_review(client):
    """反面：一位都没填的卡就是在等你去点 —— 负样本的对照组。

    没有它，上面几条即使实现改成了「谁都读成 delivering」，也照样绿。
    """
    project = _project(client)
    room = _room(client, project)
    _carded_tasks(
        client,
        project,
        room,
        [{"status": AcceptStatus.pending, "pr_number": None, "pr_url": None}],
    )

    (row,) = _rows(client, project, "tasks")
    assert row["presentation"] == {
        "column": "needs_you",
        "phrase": NeedsYou.awaiting_review,
    }
    assert row["card"] == {
        "id": row["card"]["id"],
        "status": "pending",
        "pr_number": None,
        "pr_url": None,
    }
