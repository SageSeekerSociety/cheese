"""读一道题的测试点与评测结果（方案 §看结果、§数据结构）。

评测结果由评测机器产出（``app.domain.judge``：学生交了作业就排队，评测主机跑完写回）。
这里是读它的那几条：提交页那一块「将要评测的代码」、一版提交的评测结果、老师展开某一
个用例；外加出题人用参考答案试跑（排一次、读结果）。

裁剪的规矩和 ``judge_queue.run_to_api`` 一致：状态与「通过几个 / 一共几个」谁都能看；
逐个用例的输入、期望输出和实际输出只给能批改的人（出题人、空间管理员），学生拿到的
是名称、过没过和时间。隐藏用例的字节从来没有进过学生项目，自然也不进他读取的响应。

路由：

* ``GET .../participants/{participantId}/code-version`` —— 提交页那块「将要评测的代
  码」：默认分支现在指着的那个 commit，带最后一次提交的说明和推送时间。读得到它的
  人和读这版提交的人一样（``claim.read``）；项目没有、连的是 GitHub、或者托管方这一
  刻读不出来时 ``codeVersion`` 是 ``null``，提交页据此留空（``code_version``）。
* ``GET .../submissions/{submissionId}/judge`` —— 这一版提交最近一次评测的结果，按看
  的人裁剪。没有评测过时是 ``null``。它**不是评审**：只写着评测结果，老师的评审另存
  （``task_submission_review``），所以「只出了评测结果、老师还没批改」时这一版仍然
  显示待批改。
* ``GET .../judge/cases/{caseId}`` —— 一个用例的输入与期望输出，给能批改的人在批改窗
  口里展开隐藏用例看。只给能批改的人（``Can(teach)``）。
* ``POST /spaces/{spaceId}/test-trials`` 与 ``GET /tasks/test-trials/{runId}`` —— 出题人用参考
  答案试跑一套测试点（题目不必已经发布），只有能在那个空间出题的人能排，只有发起的人
  能读（``app.domain.judge.trials``）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from app.api.response import ok, typed_response
from app.api.space_payloads import SpaceRow
from app.api.task_claims import project_of_claim
from app.api.task_deps import (
    get_task_membership_service,
    get_task_service,
)
from app.api.write_access import ROUTE_DECIDES, Can
from app.auth import can
from app.auth import task_roles as task_seats
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.task_roles import ClaimPermission
from app.auth.task_roles import Permission as TaskPermission
from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.db.session import get_db
from app.domain.judge import trials
from app.domain.space.permissions import Permission as SpacePermission
from app.domain.task import code_version, judge_queue, test_suite
from app.domain.task.access import bind_review_path
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
)

router = APIRouter(prefix="/tasks")
#: 试跑是空间里的事：谁能排看的是空间，所以它挂在空间下面。
space_router = APIRouter(prefix="/spaces")


class TaskCodeVersion(BaseModel):
    """将要评测的那一版代码：commit、分支名、最后一次提交的说明和推送时间。

    ``pushedAt`` 是毫秒；提交的说明和时间读不出来时留空，不影响 ``sha`` 本身。
    """

    sha: str
    branch: str
    subject: str
    pushedAt: int | None


class TaskCodeVersionOut(BaseModel):
    #: 读不出来（项目没有仓库、连的是 GitHub、托管方这一刻读不出来）时是 null。
    codeVersion: TaskCodeVersion | None


class TaskJudgeCase(BaseModel):
    """评测结果里的一个用例，按看的人裁剪。

    隐藏用例的 ``input``/``expected``/``actual`` 只有能批改的人拿到；学生那边这几格
    根本不存在（不是空串 —— 空串也泄露了「它没跑出东西」）。
    """

    name: str
    visibility: str
    status: str
    points: int
    ms: int | None = None
    memoryKb: int | None = None
    #: 这个用例的 id（``GET .../judge/cases/{caseId}`` 读它的全文），只给能批改的人。
    caseId: int | None = None
    input: str | None = None
    expected: str | None = None
    actual: str | None = None
    #: 运行出错时程序打到标准错误的前 4 KB；隐藏用例只给能批改的人。
    stderr: str | None = None


class TaskJudgeRun(BaseModel):
    """一次评测的结果。``cases`` 在跑完（done/failed）之前是空表。"""

    state: str
    purpose: str
    codeSha: str
    suiteRevision: int
    passedPoints: int
    totalPoints: int
    createdAt: int
    startedAt: int | None
    finishedAt: int | None
    machineMs: int | None
    error: dict | None = None
    compileOutput: str | None = None
    cases: list[TaskJudgeCase] = []
    passedCases: int = 0
    caseCount: int = 0


class TaskJudgeOut(BaseModel):
    #: 这一版还没有评测过时是 null。
    judge: TaskJudgeRun | None


class TaskJudgeCaseDetail(BaseModel):
    """一个用例的配置：输入与期望输出，给能批改的人在批改窗口里展开隐藏用例看。"""

    id: int
    index: int
    name: str
    points: int
    visibility: str
    input: str
    expected: str


class TaskJudgeCaseOut(BaseModel):
    case: TaskJudgeCaseDetail


@router.get(
    "/{taskId}/participants/{participantId}/code-version",
    summary="The Code Version That Would Be Judged",
    **typed_response(TaskCodeVersionOut),
    dependencies=[Can(TaskPermission.view)],
)
async def get_task_code_version(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    task_service: TaskService = Depends(get_task_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """提交页那块「将要评测的代码」（方案 §提交）。

    读它的人和读这一版提交的人一样（``claim.read``）：提交者本人、小队成员，以及能批改
    的人。默认分支现在指着的那个 commit；读不到就 ``codeVersion`` 是 ``null`` —— 提交
    页据此留空，而不是拦住他提交（``code_version`` 模块开头那几条）。
    """
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)
    await can.require(db, auth_user.user_id, ClaimPermission.read, membership)

    project = await project_of_claim(
        db, task=task, membership=membership, user_id=auth_user.user_id
    )
    version = (
        await code_version.for_project_with_commit(db, project.id)
        if project is not None
        else None
    )
    return ok({"codeVersion": version.to_api() if version is not None else None})


@router.get(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/judge",
    summary="A Submission's Judge Result",
    **typed_response(TaskJudgeOut),
    dependencies=[Can(TaskPermission.view)],
)
async def get_task_submission_judge(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """这一版提交最近一次评测的结果（方案 §看结果）。

    读者就是这一版提交的读者（``claim.read``）。能批改的人读到每个用例的输入、期望和
    实际输出；学生读到名称、过没过和时间 —— 隐藏用例的字节不进这份响应
    （``judge_queue.run_to_api``）。这一版没排过评测（没挂测试点、没记下代码版本）时
    是 ``null``。
    """
    task, membership = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    await can.require(db, auth_user.user_id, ClaimPermission.read, membership)
    teacher = await task_seats.teaches(db, task, auth_user.user_id)
    run = await judge_queue.latest_run(db, submission_id)
    return ok({"judge": judge_queue.run_to_api(run, teacher=teacher)})


@router.get(
    "/{taskId}/judge/cases/{caseId}",
    summary="One Test Case",
    **typed_response(TaskJudgeCaseOut),
    dependencies=[Can(TaskPermission.teach)],
)
async def get_task_judge_case(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    case_id: Annotated[int, Path(ge=1, alias="caseId")],
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """一个用例的输入与期望输出，给能批改的人在批改窗口里展开隐藏用例看。

    只给能批改的人（``Can(teach)``）：这正是「老师能展开隐藏用例的输入、期望和实际输
    出」那一半（方案 §批改窗口），学生那一半由 ``run_to_api`` 的裁剪回答。``caseId``
    是这个用例自己的 id；它属于另一道题时 404（路径没指向任何东西，和
    ``bind_review_path`` 同口径）。
    """
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    case = await test_suite.case_of_task(db, task_id, case_id)
    if case is None:
        raise NotFoundError.for_resource("test_case", case_id)
    return ok({"case": await test_suite.case_to_api(db, case)})


class TaskJudgeSummary(BaseModel):
    """一版提交最近一次评测的摘要：参与者列表那一列「测试点」读它。"""

    submissionId: int
    state: str
    passedCases: int
    caseCount: int
    passedPoints: int
    totalPoints: int


class TaskJudgeSummariesOut(BaseModel):
    summaries: list[TaskJudgeSummary]


#: 一次最多问几版提交：参与者列表一页最多 200 人。
MAX_SUMMARY_IDS = 200


@router.get(
    "/{taskId}/judge/summaries",
    summary="Judge Summaries of Submissions",
    **typed_response(TaskJudgeSummariesOut),
    dependencies=[Can(TaskPermission.teach)],
)
async def get_task_judge_summaries(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    submission_ids: Annotated[str, Query(alias="submissionIds", max_length=4000)] = "",
    db=Depends(get_db),
) -> dict:
    """几版提交各自最近一次评测的摘要（方案 §参与者列表「测试点」一列）。

    只给能批改的人（``Can(teach)``），和参与者列表同一拨人。``submissionIds`` 是逗号
    分隔的提交 id，最多 ``MAX_SUMMARY_IDS`` 个；不属于这道题的、没评测过的不在结果里。
    还没跑完的那一次只有状态，通过数是 0。
    """
    ids: list[int] = []
    for part in submission_ids.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise BadRequestError(say("judgeSummaryBadIds"))
        ids.append(int(part))
    if len(ids) > MAX_SUMMARY_IDS:
        raise BadRequestError(say("judgeSummaryTooMany", max=MAX_SUMMARY_IDS))
    runs = await judge_queue.latest_runs(db, ids)
    out: list[dict] = []
    for submission_id, run in runs.items():
        if run.task_id != task_id:
            continue
        shown = judge_queue.run_to_api(run, teacher=True) or {}
        out.append(
            {
                "submissionId": submission_id,
                "state": run.state,
                "passedCases": int(shown.get("passedCases") or 0),
                "caseCount": int(shown.get("caseCount") or 0),
                "passedPoints": int(shown.get("passedPoints") or 0),
                "totalPoints": int(shown.get("totalPoints") or 0),
            }
        )
    return ok({"summaries": out})


class TaskTestTrialFile(BaseModel):
    """参考代码里的一份文件：仓库里的相对路径和它的文本。"""

    path: str = Field(max_length=255)
    content: str


class TaskTestTrialRequest(BaseModel):
    """用参考答案试跑一套测试点（方案 §出题人：配测试点），空间在路径里。

    ``testSuite`` 和出题表单里 ``testSuite`` 的一项同形（``kind``、``runtime``、
    ``runCmd``、``cases`` ……），题目不必已经发布。参考代码二选一：``files`` 几份文本
    文件，或 ``zip`` 一个小 zip 的 base64（最多 2 MB）。
    """

    testSuite: dict
    files: list[TaskTestTrialFile] | None = None
    zip: str | None = None


class TaskTestTrial(TaskJudgeRun):
    """一次试跑：评测结果（出题人看得到每个用例的输入、期望和实际输出），加上最慢的
    那个用例用了多久、时限是多少，好看出还有多少余量。"""

    runId: int
    timeLimitMs: int
    slowestMs: int | None = None


class TaskTestTrialOut(BaseModel):
    trial: TaskTestTrial


@space_router.post(
    "/{spaceId}/test-trials",
    summary="Try a Test Suite With Reference Code",
    **typed_response(TaskTestTrialOut),
)
async def post_task_test_trial(
    space: Annotated[SpaceRow, Can(SpacePermission.publish)],
    payload: TaskTestTrialRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """排一次试跑（方案 §出题人：配测试点）。

    能在这个空间出题的人才能试跑（``space`` 的 ``publish``）；看不到这个空间的人 404。
    试跑和学生提交的评测走同一条队列，算力同样记在空间所有者头上；评测主机没接入、
    价格没设时，这一次很快以「评测暂不可用」标失败，不扣钱。返回刚排上的这一次（还在
    排队），之后用 ``GET /tasks/test-trials/{runId}`` 读结果。
    """
    lines = test_suite.suite_lines([payload.testSuite])
    if not lines:
        raise BadRequestError(say("judgeTrialNeedsSuite"))
    archive = trials.code_archive(
        [f.model_dump() for f in payload.files or []], payload.zip
    )
    run = await trials.create_trial(
        db,
        space_id=space.id,
        line=lines[0],
        code=archive,
        user_id=auth_user.user_id,
    )
    await db.commit()
    return ok({"trial": trials.trial_to_api(run)})


@router.get(
    "/test-trials/{runId:int}",
    summary="A Test Suite Trial",
    **typed_response(TaskTestTrialOut),
    dependencies=[ROUTE_DECIDES],
)
async def get_task_test_trial(
    run_id: Annotated[int, Path(ge=1, alias="runId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """一次试跑的结果。只有发起它的人读得到；别人读到的是 404。"""
    run = await trials.trial_of(db, run_id, auth_user.user_id)
    if run is None:
        raise NotFoundError.for_resource("test_trial", run_id)
    return ok({"trial": trials.trial_to_api(run)})
