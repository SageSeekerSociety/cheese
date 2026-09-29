"""卡片上那四处要后端给的数：我的档位、附件数、标签、领取上限。

这四条都是同一句话的后果 —— 板上那道题的卡片得说得出「跟我有关的那部分」。
所以下面每一条都断**接口真发回来的那个值**，不是实现怎么算的：

1. 「我的档位」只问我**本人**那条领取：别人交了什么、判了什么都不改变我的档位
   （否则卡片会把队友的进度说成我的）。领了没交是进行中，交上去没判是已提交，
   判过没通过是未通过，判过通过是已通过；没领过的题给 null，卡片那一格整块不出现。
   未通过之后再交一版，又回到「已提交」—— 那一版还没判。
2. 「附件 N」是**还挂着**的材料数：摘掉的不算，别的题上的不算，且它只是一个数 ——
   带不出文件名、大小、内容。下载权限一个字没动（那是另一条路由的事，见
   ``test_task_attachments.py``）。
3. 标签就是题目 topics 的名字，跟着列表一起回来（``queryTopics``）。
4. 「我的发布」每行的领取上限：库里 0 表示不限，接口一律折成 null —— 客户端不该
   去猜 0 说的是「不限」还是「一个都不许」。
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage as storage_module
from app.core.config import settings
from app.domain.tag.models import Tag
from tests.integration.conftest import UserCreator, create_approved_space, unique_int


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把本地存储落到 tmp_path，别往仓库里写测试文件。

    ``get_storage_backend()`` 是模块级单例，第一次调用就把 base_path 定死 —— 所以
    光改 settings 不够，还得先把已经建好的那个丢掉。收尾也丢掉一次。
    """
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    storage_module._storage_backend = None
    yield tmp_path
    storage_module._storage_backend = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _deadline_ms() -> int:
    return int((datetime.now(UTC).timestamp() + 7 * 24 * 3600) * 1000)


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    creator_token = user_client.login(api_client, creator.username, creator.password)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Board Card Fields ({suffix})",
            "intro": "一个题目板",
            "description": "卡片要的几个数从这里来",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "creator": creator,
        "creator_token": creator_token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _space_member(
    user_client: UserCreator, api_client: TestClient, board: dict
) -> tuple[object, str]:
    """一个在这块板上的人（不是管理员）—— 他看板、领题、交东西。"""
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _create_task(
    api_client: TestClient,
    board: dict,
    *,
    name: str,
    topics: list[int] | None = None,
    participant_limit: int | None = None,
    token: str | None = None,
) -> int:
    body: dict = {
        "name": name,
        "submitterType": "USER",
        "deadline": _deadline_ms(),
        "resubmittable": True,
        "editable": True,
        "intro": "题",
        "description": "题目描述",
        "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
        "space": board["space_id"],
        "categoryId": board["category_id"],
    }
    if topics is not None:
        body["topics"] = topics
    if participant_limit is not None:
        body["participantLimit"] = participant_limit
    resp = api_client.post(
        "/tasks", json=body, headers=_auth(token or board["creator_token"])
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approve = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approve.status_code == 200, approve.text
    return task_id


def _board_rows(
    api_client: TestClient,
    board: dict,
    token: str,
    *,
    query_topics: bool = False,
) -> dict[int, dict]:
    """这道板上我看得见的题，按 id 索引 —— 就是卡片列表那次请求。"""
    resp = api_client.get(
        "/tasks",
        params={
            "space": board["space_id"],
            "pageSize": 100,
            "queryJoined": "true",
            "queryTopics": "true" if query_topics else "false",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return {row["id"]: row for row in resp.json()["data"]["tasks"]}


def _claim(api_client: TestClient, task_id: int, token: str) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participant"]["id"]


def _approve_claimant(
    api_client: TestClient, board: dict, task_id: int, membership_id: int
) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _submit(
    api_client: TestClient, task_id: int, membership_id: int, token: str, text: str
) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        json=[{"text": text}],
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission"]["id"]


def _review(
    api_client: TestClient,
    board: dict,
    task_id: int,
    membership_id: int,
    submission_id: int,
    *,
    accepted: bool,
) -> None:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions/"
        f"{submission_id}/review",
        json={"accepted": accepted, "score": 5, "comment": "看看"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _upload_to_task(
    api_client: TestClient, task_id: int, token: str, *, filename: str
) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/attachments",
        headers=_auth(token),
        files={"file": (filename, io.BytesIO(b"%PDF-1.4 material"), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["attachment"]["id"]


def _create_tags_in_db(
    db_session: AsyncSession,
    portal: BlockingPortal,
    names: list[str],
    created_by: int,
) -> list[int]:
    tags = [
        Tag(name=name, created_by_id=created_by, created_at=datetime.now(UTC))
        for name in names
    ]

    async def _do() -> list[int]:
        for tag in tags:
            db_session.add(tag)
        await db_session.flush()
        return [tag.id for tag in tags]

    return portal.call(_do)


# --- 一、我的领取档位 ---------------------------------------------------------


@pytest.fixture
def one_claim_per_stage(api_client: TestClient, user_client: UserCreator) -> dict:
    """同一块板上五道题：没领、领了没交、交了没判、判了通过、判了没通过。"""
    board = _new_board(user_client, api_client)
    _, me_token = _space_member(user_client, api_client, board)

    ids = {
        "untouched": _create_task(api_client, board, name="没人领的题"),
        "claimed": _create_task(api_client, board, name="领了没交的题"),
        "submitted": _create_task(api_client, board, name="交了没判的题"),
        "passed": _create_task(api_client, board, name="判了通过的题"),
        "rejected": _create_task(api_client, board, name="判了没通过的题"),
    }

    for stage in ("claimed", "submitted", "passed", "rejected"):
        membership_id = _claim(api_client, ids[stage], me_token)
        ids[f"{stage}_membership"] = membership_id
        if stage == "claimed":
            continue
        _approve_claimant(api_client, board, ids[stage], membership_id)
        submission_id = _submit(
            api_client, ids[stage], membership_id, me_token, f"{stage} 的作业"
        )
        if stage == "submitted":
            continue
        _review(
            api_client,
            board,
            ids[stage],
            membership_id,
            submission_id,
            accepted=stage == "passed",
        )

    return {**board, "me_token": me_token, **ids}


def test_a_card_with_no_claim_of_mine_has_no_tier(
    one_claim_per_stage: dict, api_client: TestClient
):
    """没领过的题给 null：卡片上那一格整块不出现，而不是画一个「进行中」。"""
    rows = _board_rows(api_client, one_claim_per_stage, one_claim_per_stage["me_token"])
    assert rows[one_claim_per_stage["untouched"]]["myClaimStatus"] is None


def test_my_tier_walks_from_claimed_to_submitted_to_passed(
    one_claim_per_stage: dict, api_client: TestClient
):
    """领了没交是进行中；交上去没判是已提交；判过通过是已通过。"""
    rows = _board_rows(api_client, one_claim_per_stage, one_claim_per_stage["me_token"])
    assert rows[one_claim_per_stage["claimed"]]["myClaimStatus"] == "IN_PROGRESS"
    assert rows[one_claim_per_stage["submitted"]]["myClaimStatus"] == "SUBMITTED"
    assert rows[one_claim_per_stage["passed"]]["myClaimStatus"] == "PASSED"


def test_a_rejected_submission_reads_as_rejected(
    one_claim_per_stage: dict, api_client: TestClient
):
    """判过没通过是未通过 —— 卡片要说得出来「被打回来了」这件事。"""
    rows = _board_rows(api_client, one_claim_per_stage, one_claim_per_stage["me_token"])
    assert rows[one_claim_per_stage["rejected"]]["myClaimStatus"] == "REJECTED"


def test_handing_in_again_after_a_rejection_puts_me_back_in_review(
    one_claim_per_stage: dict, api_client: TestClient
):
    """被打回来之后又交了一版：那一版还没判，所以档位回到「已提交」。

    档位说的是「我最近一次递交现在到哪了」，不是「我曾经通过没有」。
    """
    board = one_claim_per_stage
    task_id, membership_id = board["rejected"], board["rejected_membership"]
    _submit(api_client, task_id, membership_id, board["me_token"], "改好了")

    rows = _board_rows(api_client, board, board["me_token"])
    assert rows[task_id]["myClaimStatus"] == "SUBMITTED"


def test_someone_elses_claim_and_verdict_do_not_move_my_tier(
    api_client: TestClient, user_client: UserCreator
):
    """队友（或者任何一个别人）交了什么、判了多少分，都不改变我的档位。

    同一道题上两个人各自领、各自交：他的通过不是我的通过。
    """
    board = _new_board(user_client, api_client)
    _, me_token = _space_member(user_client, api_client, board)
    _, other_token = _space_member(user_client, api_client, board)
    task_id = _create_task(api_client, board, name="两个人都领了的题")

    my_membership = _claim(api_client, task_id, me_token)
    _approve_claimant(api_client, board, task_id, my_membership)
    other_membership = _claim(api_client, task_id, other_token)
    _approve_claimant(api_client, board, task_id, other_membership)

    # 他交了、还通过了 —— 我这边还停在「领了没交」。
    other_submission = _submit(
        api_client, task_id, other_membership, other_token, "他的作业"
    )
    _review(
        api_client, board, task_id, other_membership, other_submission, accepted=True
    )

    rows = _board_rows(api_client, board, me_token)
    assert rows[task_id]["myClaimStatus"] == "IN_PROGRESS"

    # 我也交了（没判）—— 现在说的是我那一档。
    _submit(api_client, task_id, my_membership, me_token, "我的作业")
    rows = _board_rows(api_client, board, me_token)
    assert rows[task_id]["myClaimStatus"] == "SUBMITTED"


# --- 二、附件 N ---------------------------------------------------------------


def test_the_card_counts_the_materials_that_are_still_attached(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """挂着两个就是 2，摘掉一个就是 1 —— 算的是还挂着的那些。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带材料的题")
    first = _upload_to_task(
        api_client, task_id, board["creator_token"], filename="一.pdf"
    )
    _upload_to_task(api_client, task_id, board["creator_token"], filename="二.pdf")

    rows = _board_rows(api_client, board, board["creator_token"])
    assert rows[task_id]["attachmentCount"] == 2

    removed = api_client.delete(
        f"/tasks/{task_id}/attachments/{first}",
        headers=_auth(board["creator_token"]),
    )
    assert removed.status_code == 204, removed.text

    rows = _board_rows(api_client, board, board["creator_token"])
    assert rows[task_id]["attachmentCount"] == 1


def test_materials_on_another_task_are_not_counted_as_mine(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """别的题上的材料不算这道题的 —— 数是按题算的，也不是全板加起来。"""
    board = _new_board(user_client, api_client)
    bare = _create_task(api_client, board, name="没材料的题")
    loaded = _create_task(api_client, board, name="有材料的题")
    _upload_to_task(
        api_client, loaded, board["creator_token"], filename="只有这道题有.pdf"
    )

    rows = _board_rows(api_client, board, board["creator_token"])
    assert rows[bare]["attachmentCount"] == 0
    assert rows[loaded]["attachmentCount"] == 1


def test_the_count_comes_without_the_files_themselves(
    api_client: TestClient, user_client: UserCreator, upload_root: Path
):
    """列表里只有个数：文件名、大小、内容、直链都不在 —— 拿材料的门还是那道门。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="带材料的题")
    _upload_to_task(api_client, task_id, board["creator_token"], filename="讲义.pdf")

    rows = _board_rows(api_client, board, board["creator_token"])
    row = rows[task_id]
    assert row["attachmentCount"] == 1
    assert "attachments" not in row
    assert "files" not in row
    assert "files.pdf" not in row


# --- 三、标签 -----------------------------------------------------------------


def test_the_list_carries_the_topic_names(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal: BlockingPortal,
):
    """列表点了名（queryTopics）就把每道题的标签一起带回来 —— 卡片上写 #标签。"""
    board = _new_board(user_client, api_client)
    names = [f"标签 ({unique_int(100000, 999999)})"]
    names.append(f"标签 ({unique_int(100000, 999999)})")
    tag_ids = _create_tags_in_db(db_session, _portal, names, board["creator"].user_id)
    tagged = _create_task(api_client, board, name="有标签的题", topics=tag_ids)
    untagged = _create_task(api_client, board, name="没标签的题")

    rows = _board_rows(api_client, board, board["creator_token"], query_topics=True)
    assert [topic["name"] for topic in rows[tagged]["topics"]] == names
    assert rows[untagged]["topics"] == []


# --- 四、领取上限 -------------------------------------------------------------


def test_publishing_rows_say_whether_there_is_a_limit(
    api_client: TestClient, user_client: UserCreator
):
    """「我发布的」每行给领取上限：设了就是那个数，不限（库里 0）一律给 null。"""
    board = _new_board(user_client, api_client)
    unlimited = _create_task(api_client, board, name="不限人数的题")
    zero = _create_task(api_client, board, name="上限写 0 的题", participant_limit=0)
    capped = _create_task(api_client, board, name="限三人的题", participant_limit=3)

    resp = api_client.get(
        f"/spaces/{board['space_id']}/me/publishing/tasks",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    rows = {row["taskId"]: row for row in resp.json()["data"]["tasks"]}

    assert rows[unlimited]["participantLimit"] is None
    assert rows[zero]["participantLimit"] is None
    assert rows[capped]["participantLimit"] == 3
    # 已经几个人领了照旧给 —— 上限和实到人数是两句话，界面要把两个都写出来。
    assert rows[capped]["participantCount"] == 0
