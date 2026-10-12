"""外站成绩同步：账号怎么存、题号怎么挂、一次同步做什么。

**一次同步**（:meth:`ExternalProblemService.sync`）：读账号 → 登进去按题号把 AC 记录
拉下来（`yoj.py`）→ 拿**平台用户名**当学号去对 → 整道题的结果一次重写，并把这次成
不成写回 ``task_external_problem``。同一道题只拉一遍页面，哪怕几个题都挂在同一个
题号上。

**名单从外面传进来。** 这个域不读题目、也不读用户：一行是 ``Student(member_id,
student_no)``，其中 ``student_no`` 是这个人**现在**在平台上的用户名。好处有两个：一是
这个域不欠任何人一条边，二是「拿什么去对」这件事留在能看见用户名的地方（名单那条路
由、或者周期任务手里的那份名单），而不是在这儿猜。

**几个刻意的取舍**

* 失败时**不删旧结果**。上一次成功的结论留着，配上「上次同步失败」，比一片空白有用
  —— 空名单看起来像「谁都没过」。代价是那些数会过期，所以界面必须把失败说显眼。
* 失败也写 ``last_synced_at``：它是「上回去外站看了」的时刻，不是「上次看成了」的
  时刻，成不成由 ``last_status`` 说。
* 用户名对不上就是「没做出来」（``solved=False``）。外站上看不见「这个人不存在」和
  「这个人没 AC 过」的区别 —— 两种都是这道题的 AC 列表里没有他。所以 ``student_no``
  留档：老师看到一个人显示「未过」而学号对不上时，能自己判断是没做还是对错了人。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.external_problem.models import (
    EXTERNAL_PROBLEM_ACCOUNT_ROW_ID,
    SOURCE_YOJ,
    ExternalProblemAccount,
    ExternalProblemResult,
    TaskExternalProblem,
)
from app.domain.external_problem.yoj import YojClient, YojError, YojSubmission

#: 外站题号的形状。**只收数字**：这个值会被拼进对面那条 SQL（``prob`` 参数在服务端
#: 是字符串拼进去的），我们这边唯一能守的就是不让别的字符出去。
PROBLEM_NO = re.compile(r"[0-9]{1,10}")

#: 平台上这行凭据密封时绑的名字。那一行 id 是固定值，绑到一个名字上比绑 ``1`` 说得
#: 清楚它属于谁（照 `domain/integration/service.py` 的 ``FEISHU_APP_BINDING``）。
ACCOUNT_BINDING = "platform-external-problem-account"

STATUS_OK = "ok"
STATUS_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Student:
    """名单上的一行：报名记录 id，和拿来对的那个学号（= 平台用户名）。"""

    member_id: int
    student_no: str


@dataclass(frozen=True)
class SyncTarget:
    """一次同步要做的一道题。"""

    task_id: int
    problem_no: str
    students: Sequence[Student]


@dataclass(frozen=True)
class SyncOutcome:
    """一道题同步完了。``error`` 空串表示成了。"""

    task_id: int
    problem_no: str
    status: str
    solved: int = 0
    error: str = ""


def seal_account(row: ExternalProblemAccount, secret: dict) -> None:
    row.secret = encrypt(
        Purpose.EXTERNAL_PROBLEM_SECRET,
        json.dumps(secret),
        bound_to=ACCOUNT_BINDING,
    )


def unseal_account(row: ExternalProblemAccount) -> dict:
    if not row.secret:
        return {}
    return json.loads(
        decrypt(Purpose.EXTERNAL_PROBLEM_SECRET, row.secret, bound_to=ACCOUNT_BINDING)
    )


def account_view(row: ExternalProblemAccount | None) -> dict:
    """账号给管理员看的样子。**永远没有密码。**

    ``configured`` 是那个页面要问的第一件事，而 ``None``（还没人配过）是一个答案，
    不是错误。
    """
    if row is None:
        return {
            "configured": False,
            "source": SOURCE_YOJ,
            "username": "",
            "updated_by": "",
            "updated_at": None,
        }
    return {
        "configured": True,
        "source": row.source,
        "username": row.username,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def binding_view(
    binding: TaskExternalProblem | None, results: Sequence[ExternalProblemResult]
) -> dict:
    """一道题的外站那一块，给名单那条路由用。``binding`` 是 None 就是没挂题号。"""
    if binding is None:
        return {
            "problemNo": "",
            "source": SOURCE_YOJ,
            "lastSyncedAt": None,
            "status": "",
            "error": "",
            "results": [],
        }
    return {
        "problemNo": binding.problem_no,
        "source": binding.source,
        "lastSyncedAt": (
            binding.last_synced_at.isoformat() if binding.last_synced_at else None
        ),
        "status": binding.last_status,
        "error": binding.last_error,
        "results": [result_view(row) for row in results],
    }


def result_view(row: ExternalProblemResult) -> dict:
    return {
        "participantId": row.member_id,
        "studentNo": row.student_no,
        "solved": row.solved,
        "acceptedAt": row.accepted_at.isoformat() if row.accepted_at else None,
    }


def _yoj_client(username: str, password: str) -> YojClient:
    return YojClient(username=username, password=password)


class ExternalProblemService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        client_factory: Callable[[str, str], YojClient] | None = None,
    ) -> None:
        self._session = session
        self._client_factory = client_factory or _yoj_client

    # ---------------------------------------------------------------- 账号

    async def account(self, source: str = SOURCE_YOJ) -> ExternalProblemAccount | None:
        return await self._session.get(
            ExternalProblemAccount, EXTERNAL_PROBLEM_ACCOUNT_ROW_ID
        )

    async def save_account(
        self, *, username: str, password: str, by: str, source: str = SOURCE_YOJ
    ) -> ExternalProblemAccount:
        """存账号。``password`` 留空 = 不改已经存下的那个（页面看不到它，无从重打）。"""
        username = username.strip()
        if not username:
            raise ValidationError(say("externalProblemAccountRequired"))
        row = await self.account(source)
        if row is None:
            row = ExternalProblemAccount(
                id=EXTERNAL_PROBLEM_ACCOUNT_ROW_ID, source=source, secret=""
            )
        if password:
            seal_account(row, {"password": password})
        elif not row.secret:
            raise ValidationError(say("externalProblemPasswordRequired"))
        row.username = username
        row.updated_by = by
        self._session.add(row)
        await self._session.flush()
        return row

    # ------------------------------------------------------------ 题号挂载

    async def binding(
        self, task_id: int, source: str = SOURCE_YOJ
    ) -> TaskExternalProblem | None:
        return await self._session.scalar(
            select(TaskExternalProblem).where(
                TaskExternalProblem.task_id == task_id,
                TaskExternalProblem.source == source,
            )
        )

    async def set_problem(
        self, *, task_id: int, problem_no: str, by: str, source: str = SOURCE_YOJ
    ) -> TaskExternalProblem:
        problem_no = problem_no.strip()
        if not PROBLEM_NO.fullmatch(problem_no):
            raise ValidationError(say("externalProblemNumberInvalid"))
        row = await self.binding(task_id, source)
        if row is None:
            row = TaskExternalProblem(
                task_id=task_id, source=source, problem_no=problem_no, created_by=by
            )
        elif row.problem_no != problem_no:
            # 换了题号，旧结果说的是另一道题 —— 留着比删掉更糟。新一次同步会填上。
            await self._session.execute(
                delete(ExternalProblemResult).where(
                    ExternalProblemResult.task_id == task_id,
                    ExternalProblemResult.source == source,
                )
            )
            row.problem_no = problem_no
            row.last_synced_at = None
            row.last_status = ""
            row.last_error = ""
        row.created_by = by
        self._session.add(row)
        await self._session.flush()
        return row

    async def clear_problem(self, *, task_id: int, source: str = SOURCE_YOJ) -> None:
        await self._session.execute(
            delete(ExternalProblemResult).where(
                ExternalProblemResult.task_id == task_id,
                ExternalProblemResult.source == source,
            )
        )
        binding = await self.binding(task_id, source)
        if binding is not None:
            await self._session.delete(binding)
        await self._session.flush()

    async def results(
        self, task_id: int, source: str = SOURCE_YOJ
    ) -> list[ExternalProblemResult]:
        found = await self._session.scalars(
            select(ExternalProblemResult)
            .where(
                ExternalProblemResult.task_id == task_id,
                ExternalProblemResult.source == source,
            )
            .order_by(ExternalProblemResult.member_id)
        )
        return list(found)

    # ---------------------------------------------------------------- 同步

    async def all_bindings(self, source: str = SOURCE_YOJ) -> list[TaskExternalProblem]:
        found = await self._session.scalars(
            select(TaskExternalProblem)
            .where(TaskExternalProblem.source == source)
            .order_by(TaskExternalProblem.task_id)
        )
        return list(found)

    async def sync(self, targets: Sequence[SyncTarget]) -> list[SyncOutcome]:
        """按题号归组同步：同一道题只拉一遍页面。"""
        if not targets:
            return []
        account = await self.account()
        if account is None or not account.secret:
            return [await self._record_failure(t, "no_account") for t in targets]
        secret = unseal_account(account)
        password = str(secret.get("password") or "")
        if not password:
            return [await self._record_failure(t, "no_account") for t in targets]

        grouped: dict[str, list[SyncTarget]] = {}
        for target in targets:
            grouped.setdefault(target.problem_no, []).append(target)

        outcomes: list[SyncOutcome] = []
        async with self._client_factory(account.username, password) as client:
            for problem_no, group in grouped.items():
                try:
                    submissions = await client.accepted_submissions(problem_no)
                except YojError as exc:
                    for target in group:
                        outcomes.append(
                            await self._record_failure(
                                target, exc.kind, detail=exc.detail
                            )
                        )
                    continue
                for target in group:
                    outcomes.append(await self._store(target, submissions))
        return outcomes

    async def _record_failure(
        self, target: SyncTarget, kind: str, detail: str = ""
    ) -> SyncOutcome:
        binding = await self.binding(target.task_id)
        error = f"{kind} {detail}".strip()
        if binding is not None:
            binding.last_status = STATUS_FAILED
            # 具体内容跟着 kind 后面：句子由界面说，这里是排查用的原文。
            binding.last_error = error
            binding.last_synced_at = _now()
        await self._session.flush()
        return SyncOutcome(
            task_id=target.task_id,
            problem_no=target.problem_no,
            status=STATUS_FAILED,
            error=error,
        )

    async def _store(
        self, target: SyncTarget, submissions: Sequence[YojSubmission]
    ) -> SyncOutcome:
        now = _now()
        earliest: dict[str, YojSubmission] = {}
        for row in submissions:
            if row.problem_no != target.problem_no:
                continue
            kept = earliest.get(row.student_no)
            if kept is None or row.accepted_at < kept.accepted_at:
                earliest[row.student_no] = row

        await self._session.execute(
            delete(ExternalProblemResult).where(
                ExternalProblemResult.task_id == target.task_id,
                ExternalProblemResult.source == SOURCE_YOJ,
            )
        )
        solved = 0
        for student in target.students:
            row = earliest.get(student.student_no)
            if row is not None:
                solved += 1
            self._session.add(
                ExternalProblemResult(
                    task_id=target.task_id,
                    member_id=student.member_id,
                    source=SOURCE_YOJ,
                    student_no=student.student_no,
                    solved=row is not None,
                    accepted_at=row.accepted_at if row is not None else None,
                    synced_at=now,
                )
            )
        binding = await self.binding(target.task_id)
        if binding is not None:
            binding.last_status = STATUS_OK
            binding.last_error = ""
            binding.last_synced_at = now
        await self._session.flush()
        return SyncOutcome(
            task_id=target.task_id,
            problem_no=target.problem_no,
            status=STATUS_OK,
            solved=solved,
        )
