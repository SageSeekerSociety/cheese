"""外站那一块的服务层：账号怎么存、题号怎么换、一次同步往库里写什么。

直调 ``ExternalProblemService``，外站用假的 ``client_factory`` 顶掉 —— 真的抓取在
``tests/unit/test_a_yoj_page_is_read_as_rows.py`` 里测。这里要证的是**我们自己这一侧
的事**：一次同步同一道题只拉一遍、失败不删旧结论、换题号把旧结论清掉、对不上的学号
是「没过」而不是「没有这个人」。

题目行直接种进库（``seed_task_with_protocol``）：这道题是哪个域的、谁出的一点都不
参与被测的事，走那条发题流程只会让用例更长。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import UnprocessableEntityError
from app.domain.external_problem.models import (
    SOURCE_YOJ,
    ExternalProblemResult,
    TaskExternalProblem,
)
from app.domain.external_problem.service import (
    STATUS_FAILED,
    STATUS_OK,
    ExternalProblemService,
    Student,
    SyncTarget,
    account_view,
    unseal_account,
)
from app.domain.external_problem.yoj import YojError, YojSubmission
from tests.conftest import seed_task_with_protocol

AT = 1_700_000_000


def _submission(problem_no: str, student_no: str, *, at: int = AT) -> YojSubmission:
    return YojSubmission(
        student_no=student_no,
        problem_no=problem_no,
        accepted_at=datetime.fromtimestamp(at, UTC),
    )


class _FakeYoj:
    """假外站：记下被怎么用的，回什么由用例定。"""

    def __init__(
        self,
        *,
        by_problem: dict[str, list[YojSubmission]] | None = None,
        fail: YojError | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.accounts: list[tuple[str, str]] = []
        self._by_problem = by_problem or {}
        self._fail = fail

    def factory(self, username: str, password: str) -> _FakeYoj:
        """``ExternalProblemService`` 要的那个 ``client_factory``。"""
        self.accounts.append((username, password))
        return self

    async def __aenter__(self) -> _FakeYoj:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def accepted_submissions(self, problem_no: str) -> list[YojSubmission]:
        self.calls.append(problem_no)
        if self._fail is not None:
            raise self._fail
        return self._by_problem.get(problem_no, [])


def _run(client, scenario):
    """在一个请求会话里跑一段业务协程：走的是请求那个循环和那个池。"""

    async def run():
        async with client.test_request_factory() as session:
            result = await scenario(session)
            await session.commit()
            return result

    return client.portal.call(run)


def _claim_row(task_id: int, member_id: int, *, solved: bool = True) -> dict:
    return {
        "task_id": task_id,
        "member_id": member_id,
        "source": SOURCE_YOJ,
        "student_no": "20210129",
        "solved": solved,
        "accepted_at": datetime.fromtimestamp(AT, UTC) if solved else None,
        "synced_at": datetime.now(UTC),
    }


def _target(
    task_id: int, students: list[tuple[int, str]], problem_no="1234"
) -> SyncTarget:
    return SyncTarget(
        task_id=task_id,
        problem_no=problem_no,
        students=[Student(member_id=i, student_no=no) for i, no in students],
    )


# ---- 账号 --------------------------------------------------------------------


def test_an_account_without_a_name_is_refused(client):
    async def scenario(session):
        with pytest.raises(UnprocessableEntityError) as caught:
            await ExternalProblemService(session).save_account(
                username="  ", password="pw", by="admin"
            )
        return caught.value.message

    assert _run(client, scenario) == "请填 YOJ 的账号"


def test_a_first_account_without_a_password_is_refused(client):
    async def scenario(session):
        with pytest.raises(UnprocessableEntityError) as caught:
            await ExternalProblemService(session).save_account(
                username="20210129", password="", by="admin"
            )
        return caught.value.message

    assert _run(client, scenario) == "请填 YOJ 账号的密码"


def test_saving_without_a_password_keeps_the_one_already_stored(client):
    """页面看不到密码，所以空串只能是「不改」，不能是「清掉」。"""

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.save_account(username="20210129", password="pw", by="admin")
        row = await service.save_account(username="teach2", password="", by="admin2")
        assert row.username == "teach2"
        assert row.updated_by == "admin2"
        return unseal_account(row)["password"]

    assert _run(client, scenario) == "pw"


def test_a_saved_password_never_appears_in_what_the_admin_reads(client):
    async def scenario(session):
        service = ExternalProblemService(session)
        row = await service.save_account(
            username="20210129", password="pw-secret", by="admin"
        )
        return account_view(row)

    view = _run(client, scenario)
    assert view["configured"] is True
    assert view["username"] == "20210129"
    assert "pw-secret" not in str(view)


# ---- 题号 --------------------------------------------------------------------


def test_changing_the_problem_number_drops_the_old_conclusions(client):
    """旧结论说的是另一道题，留着比空着更糟。"""
    task_id = seed_task_with_protocol(client)

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.set_problem(task_id=task_id, problem_no="1234", by="t")
        session.add(ExternalProblemResult(**_claim_row(task_id, 7)))
        await session.flush()

        binding = await service.set_problem(task_id=task_id, problem_no="5678", by="t")
        results = await service.results(task_id)
        return binding.problem_no, binding.last_synced_at, results

    problem_no, synced_at, rows = _run(client, scenario)
    assert problem_no == "5678"
    assert synced_at is None
    assert rows == []


def test_setting_the_same_problem_number_again_is_idempotent(client):
    task_id = seed_task_with_protocol(client)

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.set_problem(task_id=task_id, problem_no="1234", by="t")
        session.add(ExternalProblemResult(**_claim_row(task_id, 7)))
        await session.flush()

        again = await service.set_problem(task_id=task_id, problem_no="1234", by="t2")
        return again.created_by, await service.results(task_id)

    created_by, rows = _run(client, scenario)
    assert created_by == "t2"
    assert len(rows) == 1


def test_a_problem_number_that_is_not_digits_is_refused(client):
    async def scenario(session):
        with pytest.raises(UnprocessableEntityError) as caught:
            await ExternalProblemService(session).set_problem(
                task_id=1, problem_no="1234a", by="t"
            )
        return caught.value.message

    assert _run(client, scenario) == "YOJ 题号只能填数字"


def test_clearing_takes_the_number_and_the_conclusions_together(client):
    task_id = seed_task_with_protocol(client)

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.set_problem(task_id=task_id, problem_no="1234", by="t")
        session.add(ExternalProblemResult(**_claim_row(task_id, 7)))
        await session.flush()

        await service.clear_problem(task_id=task_id)
        left = await session.scalars(
            select(ExternalProblemResult).where(
                ExternalProblemResult.task_id == task_id
            )
        )
        return await service.binding(task_id), list(left)

    binding, rows = _run(client, scenario)
    assert binding is None
    assert rows == []


# ---- 同步 --------------------------------------------------------------------


def test_syncing_writes_one_row_per_claim_and_says_who_is_done(client):
    task_id = seed_task_with_protocol(client)
    fake = _FakeYoj(
        by_problem={
            "1234": [
                _submission("1234", "20210129"),
                _submission("1234", "20210130", at=AT + 60),
            ]
        }
    )

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.save_account(username="20210129", password="pw", by="admin")
        await service.set_problem(task_id=task_id, problem_no="1234", by="t")
        service._client_factory = fake.factory
        outcomes = await service.sync(
            [
                _target(
                    task_id,
                    [(11, "20210129"), (12, "20210130"), (13, "20219999")],
                )
            ]
        )
        return (
            outcomes[0],
            await service.binding(task_id),
            await service.results(task_id),
            fake.accounts,
        )

    outcome, binding, rows, accounts = _run(client, scenario)

    assert (outcome.status, outcome.solved) == (STATUS_OK, 2)
    assert (binding.last_status, binding.last_error) == (STATUS_OK, "")
    assert binding.last_synced_at is not None
    assert accounts == [("20210129", "pw")]
    by_id = {row.member_id: row for row in rows}
    assert [by_id[i].solved for i in (11, 12, 13)] == [True, True, False]
    # 对不上的那个：学号留档、时间空着，老师看得出对的是谁。
    assert by_id[13].student_no == "20219999"
    assert by_id[13].accepted_at is None
    assert by_id[12].accepted_at is not None


def test_two_tasks_on_the_same_problem_fetch_the_pages_once(client):
    first = seed_task_with_protocol(client)
    second = seed_task_with_protocol(client)
    fake = _FakeYoj(by_problem={"1234": [_submission("1234", "20210129")]})

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.save_account(username="20210129", password="pw", by="admin")
        service._client_factory = fake.factory
        await service.sync(
            [
                _target(first, [(11, "20210129")]),
                _target(second, [(21, "20210129")]),
            ]
        )
        return fake.calls, await service.results(second)

    calls, rows = _run(client, scenario)
    assert calls == ["1234"]
    assert [row.solved for row in rows] == [True]


def test_a_failed_sync_keeps_the_conclusions_it_could_not_replace(client):
    """一片空白看起来像「谁都没过」，所以失败时留着上一次的数，只把状态改成 failed。"""
    task_id = seed_task_with_protocol(client)
    working = _FakeYoj(by_problem={"1234": [_submission("1234", "20210129")]})
    broken = _FakeYoj(fail=YojError("unreadable", 7))
    target = _target(task_id, [(11, "20210129")])

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.save_account(username="20210129", password="pw", by="admin")
        await service.set_problem(task_id=task_id, problem_no="1234", by="t")
        service._client_factory = working.factory
        await service.sync([target])

        service._client_factory = broken.factory
        outcomes = await service.sync([target])
        binding = await service.binding(task_id)
        return outcomes[0], binding, await service.results(task_id)

    outcome, binding, rows = _run(client, scenario)

    assert (outcome.status, outcome.error) == (STATUS_FAILED, "unreadable 7")
    assert (binding.last_status, binding.last_error) == (STATUS_FAILED, "unreadable 7")
    assert binding.last_synced_at is not None
    assert [row.solved for row in rows] == [True]


def test_syncing_without_an_account_never_reaches_the_external_site(client):
    task_id = seed_task_with_protocol(client)
    fake = _FakeYoj()

    async def scenario(session):
        service = ExternalProblemService(session)
        await service.set_problem(task_id=task_id, problem_no="1234", by="t")
        service._client_factory = fake.factory
        outcomes = await service.sync([_target(task_id, [(11, "20210129")])])
        return outcomes[0], fake.calls, await service.binding(task_id)

    outcome, calls, binding = _run(client, scenario)
    assert (outcome.status, outcome.error) == (STATUS_FAILED, "no_account")
    assert calls == []
    assert binding.last_status == STATUS_FAILED


def test_a_problem_number_is_stored_as_the_external_site_wrote_it(client):
    """存字符串，不存数：``0012`` 和 ``12`` 在外站是两回事。"""
    task_id = seed_task_with_protocol(client)

    async def scenario(session):
        await ExternalProblemService(session).set_problem(
            task_id=task_id, problem_no="0012", by="t"
        )
        row = await session.scalar(
            select(TaskExternalProblem).where(TaskExternalProblem.task_id == task_id)
        )
        return row.problem_no, row.source, row.last_status, row.last_error

    assert _run(client, scenario) == ("0012", SOURCE_YOJ, "", "")
