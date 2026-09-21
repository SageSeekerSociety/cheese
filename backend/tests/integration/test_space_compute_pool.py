"""空间级额度池 — one course, one shared budget (2026-09-14 decision).

A 老师 (or 三创中心) funds a Space; every project whose 赛题 was published under
that Space draws on the same balance, first come first served and capped in
total. This file is the acceptance suite for that, and it deliberately asserts
the three properties the decision came with, because each of them is something
a later change could quietly undo:

* the pool is genuinely SHARED — what one project spends, every other project
  in the Space both sees and can no longer spend;
* it can be exhausted by one student, after which the whole class is refused
  with the platform's own 额度已用完 notice in the room;
* it reaches ONLY projects published under that Space. A project made from the
  rail has no 赛题, so nothing on it names a pool, and a course's budget is not
  reachable from outside the course.

The pool can also overspend by roughly one turn, by design — the gate is
checked on the way in and the charge lands when the turn settles. Tests below
assert the overdraft exists rather than pretending it does not.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import seed_space, seed_task_with_protocol, wait_work_idle
from tests.integration.conftest import chat_ws_url

# The stub agent reports 10 input + 5 output tokens per turn; at the default
# rate (1 credit = 10k tokens) one turn costs 0.0015 credits.
CREDITS_PER_TURN = 15 / 10_000

STUDENT = "u1"
TEACHER = "teacher"


# --- fixtures ----------------------------------------------------------------


def _login(client, handle: str) -> tuple[str, int]:
    """A real DB user, returning (session token, user id).

    `seed_user` returns only the token, and the Space-admin rule keys off the
    numeric user id, so this test file needs both.
    """
    from app.common.auth import create_access_token
    from app.domain.user.repositories import UserRepository

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = UserRepository(session)
            user = await repo.get_by_username(handle)
            if user is None:
                user = await repo.create_user(
                    username=handle, email=f"{handle}@example.com"
                )
            holder["id"] = user.id
            await session.commit()

    asyncio.run(_seed())
    return create_access_token(holder["id"], handle=handle), holder["id"]


def _auth(client, token: str) -> None:
    client.headers["Authorization"] = f"Bearer {token}"


def _space_with_admin(client, admin_user_id: int, name: str | None = None) -> int:
    """A Space plus the admin relation that lets someone fund its pool."""
    from app.domain.space.models import SpaceAdminRelation, SpaceAdminRole

    space_id = seed_space(client, name=name or f"信院-{uuid.uuid4().hex[:8]}")

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            session.add(
                SpaceAdminRelation(
                    space_id=space_id,
                    user_id=admin_user_id,
                    role=SpaceAdminRole.OWNER.value,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await session.commit()

    asyncio.run(_seed())
    return space_id


def _mk_project(client, token: str, name: str, *, from_task: int | None = None) -> str:
    _auth(client, token)
    body: dict = {"name": name}
    if from_task is not None:
        body["external_task_id"] = from_task
    response = client.post("/projects", json=body)
    assert response.status_code == 200
    return response.json()["data"]["id"]


def _fund(client, token: str, space_id: int, credits: float) -> dict:
    _auth(client, token)
    response = client.post(
        f"/spaces/{space_id}/compute-pool", json={"credits": credits}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _pool(client, token: str, space_id: int) -> dict:
    _auth(client, token)
    response = client.get(f"/spaces/{space_id}/compute-pool")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _credits(client, token: str, project_id: str) -> dict:
    _auth(client, token)
    response = client.get(f"/projects/{project_id}/credits")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _mk_topic(client, token: str, project_id: str) -> str:
    _auth(client, token)
    response = client.post(
        "/topics",
        json={"project_id": project_id, "title": "聊聊", "created_by": STUDENT},
    )
    assert response.status_code == 200
    return response.json()["data"]["id"]


def _run_turn(client, token: str, topic_id: str) -> list[dict]:
    """One summoned turn over the WS; returns every frame up to done/error."""
    _auth(client, token)
    frames: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, STUDENT)) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                break
    wait_work_idle()
    return frames


def _room_text(client, token: str, topic_id: str) -> str:
    _auth(client, token)
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return "\n".join(b.get("content") or "" for b in blocks)


def _inbox(client, token: str, type_: str) -> list[dict]:
    _auth(client, token)
    response = client.get("/notifications", params={"type": type_})
    assert response.status_code == 200, response.text
    return response.json()["data"]["notifications"]


# --- the pool is shared ------------------------------------------------------


def test_a_teacher_funds_one_pool_the_whole_class_spends(client):
    """A 花掉的 B 看得见，也花得到: the balance moves for everyone in the Space."""
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)
    # resource_pack={} so the projects get NO earmarked grant — the Space pool
    # is then the only thing they can spend, which is what makes the sharing
    # assertion mean something.
    task_id = seed_task_with_protocol(client, resource_pack={}, space_id=space_id)

    a = _mk_project(client, student_token, "A 队", from_task=task_id)
    b = _mk_project(client, student_token, "B 队", from_task=task_id)

    _fund(client, teacher_token, space_id, 1)

    for project in (a, b):
        before = _credits(client, student_token, project)
        assert before["unlimited"] is False
        assert before["credits_total"] == 1
        assert before["credits_used"] == 0

    # A spends.
    _run_turn(client, student_token, _mk_topic(client, student_token, a))
    a_after = _credits(client, student_token, a)
    assert a_after["credits_used"] == pytest.approx(CREDITS_PER_TURN)

    # B sees A's spend — same pool, not a private copy.
    b_after = _credits(client, student_token, b)
    assert b_after["credits_used"] == pytest.approx(CREDITS_PER_TURN)
    assert b_after["credits_remaining"] == pytest.approx(1 - CREDITS_PER_TURN)

    # And B can spend it too.
    _run_turn(client, student_token, _mk_topic(client, student_token, b))
    assert _credits(client, student_token, b)["credits_used"] == pytest.approx(
        2 * CREDITS_PER_TURN
    )

    # 谁花了多少: the teacher's view names both projects and the one total.
    pool = _pool(client, teacher_token, space_id)
    assert pool["credits_total"] == 1
    assert pool["credits_used"] == pytest.approx(2 * CREDITS_PER_TURN)
    assert pool["credits_remaining"] == pytest.approx(1 - 2 * CREDITS_PER_TURN)
    rows = {row["project_id"]: row for row in pool["projects"]}
    assert set(rows) == {a, b}
    assert rows[a]["cost_usd"] > 0
    assert rows[b]["cost_usd"] > 0


def test_a_top_up_adds_to_the_pool_and_does_not_erase_what_was_spent(client):
    """充值 is a new grant, not a reset — otherwise the ledger stops reconciling."""
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)
    task_id = seed_task_with_protocol(client, resource_pack={}, space_id=space_id)
    project = _mk_project(client, student_token, "A 队", from_task=task_id)

    _fund(client, teacher_token, space_id, 1)
    _run_turn(client, student_token, _mk_topic(client, student_token, project))

    _fund(client, teacher_token, space_id, 2)

    summary = _credits(client, student_token, project)
    assert summary["credits_total"] == 3
    assert summary["credits_used"] == pytest.approx(CREDITS_PER_TURN)


def test_earmarked_credits_are_spent_before_the_class_pool(client):
    """项目专属 → 空间共享: a team's own pack must burn first.

    Otherwise one team's spending eats the pool its classmates also rely on
    while its own earmarked credits sit untouched.
    """
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)
    task_id = seed_task_with_protocol(
        client, resource_pack={"compute_credits": 1}, space_id=space_id
    )
    project = _mk_project(client, student_token, "A 队", from_task=task_id)
    _fund(client, teacher_token, space_id, 1)

    _run_turn(client, student_token, _mk_topic(client, student_token, project))

    pool = _pool(client, teacher_token, space_id)
    assert pool["credits_used"] == 0  # the Space pool was not touched
    by_source = {
        g["source_task_id"]: g
        for g in _credits(client, student_token, project)["grants"]
    }
    assert by_source[task_id]["credits_used"] == pytest.approx(CREDITS_PER_TURN)


def test_one_student_can_exhaust_the_class_pool_and_the_class_is_refused(client):
    """烧光后全班被拒，且房间里写明原因."""
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)
    task_id = seed_task_with_protocol(client, resource_pack={}, space_id=space_id)

    a = _mk_project(client, student_token, "A 队", from_task=task_id)
    b = _mk_project(client, student_token, "B 队", from_task=task_id)

    # Less than one turn: A is admitted (the gate saw a positive balance on the
    # way in) and the settlement overdraws the pool. That overdraft is the
    # documented behaviour, so assert it rather than hide it.
    _fund(client, teacher_token, space_id, 0.001)

    first = _run_turn(client, student_token, _mk_topic(client, student_token, a))
    assert first[-1]["type"] == "done"
    assert _credits(client, student_token, a)["credits_remaining"] < 0

    # B inherits the empty pool — the class is refused, not just A.
    b_topic = _mk_topic(client, student_token, b)
    second = _run_turn(client, student_token, b_topic)
    types = [frame["type"] for frame in second]
    assert types == ["user_block", "event_block", "error"]
    assert "tokens 额度已用完" in second[1]["block"]["content"]

    # And the reason is written into B's room, not only in a transient frame.
    assert "tokens 额度已用完" in _room_text(client, student_token, b_topic)


# --- the 80% warning ---------------------------------------------------------


def test_the_teacher_is_warned_once_when_the_pool_passes_eighty_percent(client):
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)
    task_id = seed_task_with_protocol(client, resource_pack={}, space_id=space_id)
    a = _mk_project(client, student_token, "A 队", from_task=task_id)
    b = _mk_project(client, student_token, "B 队", from_task=task_id)

    # One turn (0.0015) against a 0.0018 pool is 83%, past the warn mark and
    # deliberately not 100% — the mark is what is under test, not exhaustion.
    _fund(client, teacher_token, space_id, 0.0018)
    assert _inbox(client, teacher_token, "SPACE_COMPUTE_POOL_LOW") == []

    _run_turn(client, student_token, _mk_topic(client, student_token, a))

    warnings = _inbox(client, teacher_token, "SPACE_COMPUTE_POOL_LOW")
    assert len(warnings) == 1
    assert warnings[0]["contextMetadata"]["spaceId"] == str(space_id)
    assert warnings[0]["contextMetadata"]["ratio"] >= 0.8

    # A second turn, still past the mark, must not post a second warning.
    _run_turn(client, student_token, _mk_topic(client, student_token, b))
    assert len(_inbox(client, teacher_token, "SPACE_COMPUTE_POOL_LOW")) == 1


# --- what the pool does NOT reach -------------------------------------------


def test_a_project_made_without_a_task_cannot_reach_any_course_pool(client):
    """自治项目没有 external_task_id，花不到任何课程池."""
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)
    # A 赛题 in this Space, funded — and a project that came from no 赛题.
    seed_task_with_protocol(client, resource_pack={}, space_id=space_id)
    _fund(client, teacher_token, space_id, 100)

    rail = _mk_project(client, student_token, "自己攒的项目")

    summary = _credits(client, student_token, rail)
    assert summary["unlimited"] is True
    assert summary["grants"] == []

    frames = _run_turn(client, student_token, _mk_topic(client, student_token, rail))
    assert frames[-1]["type"] == "done"

    # The turn ran, and the course's budget did not move.
    pool = _pool(client, teacher_token, space_id)
    assert pool["credits_used"] == 0
    assert pool["credits_remaining"] == 100


def test_a_project_in_another_course_does_not_touch_this_pool(client):
    """一个班的池子不会被别的课程的项目花掉."""
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    funded_space = _space_with_admin(client, teacher_id)
    other_space = _space_with_admin(client, teacher_id)

    other_task = seed_task_with_protocol(client, resource_pack={}, space_id=other_space)
    outsider = _mk_project(client, student_token, "外系项目", from_task=other_task)

    _fund(client, teacher_token, funded_space, 100)
    _run_turn(client, student_token, _mk_topic(client, student_token, outsider))

    assert _credits(client, student_token, outsider)["unlimited"] is True
    assert _pool(client, teacher_token, funded_space)["credits_used"] == 0


# --- authorization -----------------------------------------------------------


def test_only_a_space_admin_can_fund_the_pool(client):
    teacher_token, teacher_id = _login(client, TEACHER)
    student_token, _ = _login(client, STUDENT)
    space_id = _space_with_admin(client, teacher_id)

    _auth(client, student_token)
    response = client.post(f"/spaces/{space_id}/compute-pool", json={"credits": 100})
    assert response.status_code == 403
    assert _pool(client, teacher_token, space_id)["has_pool"] is False


def test_a_space_with_no_pool_has_none_rather_than_unlimited(client):
    """没挂池 ≠ 无限: 老师的池子视图不能把「没买」说成「不限」."""
    teacher_token, teacher_id = _login(client, TEACHER)
    space_id = _space_with_admin(client, teacher_id)

    pool = _pool(client, teacher_token, space_id)
    assert pool["has_pool"] is False
    assert pool["credits_remaining"] == 0
    assert pool["exhausted"] is False
    assert pool["needs_attention"] is False


@pytest.mark.parametrize("credits", [0, -1])
def test_a_non_positive_funding_amount_is_refused(client, credits):
    teacher_token, teacher_id = _login(client, TEACHER)
    space_id = _space_with_admin(client, teacher_id)

    _auth(client, teacher_token)
    response = client.post(
        f"/spaces/{space_id}/compute-pool", json={"credits": credits}
    )
    # The request model's gt=0 rejects these; the platform's global
    # validation_exception_handler reports that as its own 400, not a 422.
    assert response.status_code == 400
    assert _pool(client, teacher_token, space_id)["has_pool"] is False


def test_an_infinite_funding_amount_is_refused_before_it_reaches_the_ledger(client):
    """inf gets past JSON parsing AND past `gt=0` — `inf > 0` is True.

    Nothing above the repository stops it, so without the guard it would land
    in `credits_total` and then make every comparison in `consume` false: the
    pool would look bottomless and silently stop refusing anyone. Sent as a
    raw body because httpx will not serialise `inf` itself.
    """
    teacher_token, teacher_id = _login(client, TEACHER)
    space_id = _space_with_admin(client, teacher_id)

    _auth(client, teacher_token)
    response = client.post(
        f"/spaces/{space_id}/compute-pool",
        content=b'{"credits": Infinity}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert _pool(client, teacher_token, space_id)["has_pool"] is False


@pytest.mark.anyio
async def test_the_credit_guard_rejects_nan_and_infinity():
    """The guard itself, independent of how a request reached it."""
    from app.domain.usage.repositories import _require_positive_credits

    for bad in (float("inf"), float("-inf"), float("nan"), 0.0, -1.0):
        with pytest.raises(ValueError):
            _require_positive_credits(bad)
    _require_positive_credits(0.5)  # and a real amount still passes
