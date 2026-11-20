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
  能读（``app.domain.judge.trials``）。三种形态都能试跑。
* ``POST .../participants/{participantId}/self-tests`` 与 ``GET .../self-tests/latest``
  —— 学生自测：拿默认分支现在的代码只跑这一阶段的样例用例，不计分，每人每天有次数
  （``judge_queue.enqueue_self_test``）。这一步只有输入/输出用例能自测。
* ``POST /tasks/{taskId}/judge/rejudge`` —— 老师改了测试点以后，把每个参与者最新的那一
  版提交按现在这一版测试再评一次（``judge_queue.rejudge_stage``）。
"""

from datetime import UTC, datetime
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
from app.domain.task.stages import open_stage, stage_index
from app.domain.task.submission_state import past_deadline

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
    #: 测试脚本：报告里的测试名（``classname::name``）。
    testId: str | None = None
    #: 测试脚本：这个测试的失败信息；老师都看得到，学生只看得到样例那几条。
    message: str | None = None
    #: 命令判分：这一项的得分与满分（可以是小数）；``points`` 是满分取整。
    score: float | None = None
    maxScore: float | None = None
    #: 测试脚本：这个测试被跳过了（算没过，``status`` 是 ``wrong``）。
    skipped: bool | None = None
    #: 测试脚本：报告里有、出题人没给它配分的测试（0 分，只列出来）。
    unscored: bool | None = None
    #: 命令判分：score.json 里 ``visible: false`` 的那一项；只有老师读得到这一行。
    private: bool | None = None


class TaskJudgeRun(BaseModel):
    """一次评测的结果。``cases`` 在跑完（done/failed）之前是空表。

    ``state`` 是 ``done`` 而 ``scoreFailed`` 为真：判分失败（测试脚本没写出报告、判分
    命令没给出分或分不在范围里），``error`` 说是哪一种，两个分数是 0 但不算数 —— 评分
    要点里那一行按「待定」处理，老师看 ``compileOutput``（程序输出前 200 行）手动给分。
    """

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
    #: 出题人允许学生看的程序输出（前 ``studentLogLines`` 行）；没有就不出现。
    studentOutput: str | None = None
    scoreFailed: bool = False
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
#: 提交 id 是 BIGINT：比这大的数不指任何一行。
MAX_SUBMISSION_ID = 2**63 - 1


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
        # 只认 ASCII 十进制数字：「①」「²」也过得了 isdigit，int() 却不收；大过 BIGINT 的
        # 数不指任何一行，绑进查询反而报错。
        if not (part.isascii() and part.isdigit()) or int(part) > MAX_SUBMISSION_ID:
            raise BadRequestError(say("judgeSummaryBadIds"))
        ids.append(int(part))
    if len(ids) > MAX_SUMMARY_IDS:
        raise BadRequestError(say("judgeSummaryTooMany", max=MAX_SUMMARY_IDS))
    runs = await judge_queue.latest_runs(db, ids)
    out: list[dict] = []
    for submission_id, run in runs.items():
        if run.task_id != task_id:
            continue
        # 只要几个数：直接从结果行数，不像 ``run_to_api`` 那样把每个用例的输入输出都抄一遍。
        cases = [c for c in run.results or [] if isinstance(c, dict)]
        done = run.state not in judge_queue.IN_FLIGHT
        out.append(
            {
                "submissionId": submission_id,
                "state": run.state,
                "passedCases": sum(1 for c in cases if c.get("status") == "ok")
                if done
                else 0,
                "caseCount": len(cases) if done else 0,
                "passedPoints": int(run.passed_points or 0),
                "totalPoints": int(run.total_points or 0),
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


class TaskSelfTestOut(BaseModel):
    """最近一次自测和今天的次数。

    ``selfTest`` 还没自测过时是 null；``dailyLimit`` 是出题人给的每天次数（没开自测是
    null），``usedToday`` 是今天（UTC）已经用掉的，``resetsAt``（毫秒）是下一次清零。
    """

    selfTest: TaskJudgeRun | None
    dailyLimit: int | None
    usedToday: int
    resetsAt: int


async def _self_test_view(db, *, task_id: int, membership_id: int, stage: int) -> dict:
    suite = await test_suite.suite_of_stage(db, task_id, stage)
    if suite is None:
        raise NotFoundError.for_resource("test_suite", stage)
    run = await judge_queue.latest_self_test(
        db, task_id=task_id, membership_id=membership_id, suite_id=suite.id
    )
    used = await judge_queue.self_tests_today(
        db, task_id=task_id, membership_id=membership_id, suite_id=suite.id
    )
    return {
        # 自测只跑样例用例，输入输出本来就公开，编译输出也该让学生看见：按老师那一份给。
        "selfTest": judge_queue.run_to_api(run, teacher=True),
        "dailyLimit": suite.self_test_daily,
        "usedToday": used,
        "resetsAt": int(judge_queue.next_reset().timestamp() * 1000),
    }


@router.post(
    "/{taskId}/participants/{participantId}/self-tests",
    summary="Self-Test the Sample Cases",
    **typed_response(TaskSelfTestOut),
    dependencies=[Can(TaskPermission.view)],
)
async def post_task_self_test(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    stage: str | None = Query(default=None, max_length=16),
    task_service: TaskService = Depends(get_task_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """排一次自测（方案 §自测样例）：默认分支现在指着的那一版代码，只跑这一阶段的
    样例用例，不计分、不挂在哪一版提交上。

    和交作业同一道门：能替这份领题交作业的人（``claim.hand_in``），领题已通过、题目
    没结束、没过领题的截止时间、这一阶段没过截止日期。另有：这一阶段的测试点是输入/
    输出用例、出题人开了自测（``selfTestDaily``）、今天（UTC）的次数还没用完。返回刚排
    上的这一次和今天的次数，之后用 ``GET .../self-tests/latest`` 读结果。
    """
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)
    await can.require(db, auth_user.user_id, ClaimPermission.hand_in, membership)
    if membership.approved != 0:
        raise BadRequestError(say("submissionClaimNotApproved"))
    now = datetime.now(UTC)
    if task.ended_at is not None:
        raise BadRequestError(say("submissionChallengeEnded"))
    if past_deadline(membership.deadline, now):
        raise BadRequestError(say("submissionPastDeadline"))
    stage_at = stage_index(stage, await task_service.stage_keys(task_id))
    await open_stage(db, task_id, stage_at, now)

    project = await project_of_claim(
        db, task=task, membership=membership, user_id=auth_user.user_id
    )
    version = (
        await code_version.for_project(db, project.id) if project is not None else None
    )
    await judge_queue.enqueue_self_test(
        db,
        task_id=task_id,
        stage=stage_at,
        membership_id=membership.id,
        project_id=project.id if project is not None else None,
        code_sha=version.sha if version is not None else "",
        requested_by=auth_user.user_id,
    )
    view = await _self_test_view(
        db, task_id=task_id, membership_id=membership.id, stage=stage_at
    )
    await db.commit()
    return ok(view)


@router.get(
    "/{taskId}/participants/{participantId}/self-tests/latest",
    summary="The Latest Self-Test",
    **typed_response(TaskSelfTestOut),
    dependencies=[Can(TaskPermission.view)],
)
async def get_task_self_test(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    stage: str | None = Query(default=None, max_length=16),
    task_service: TaskService = Depends(get_task_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """这个参与者在这一阶段最近一次自测和今天的次数。读者和读这份领题的提交的人一样
    （``claim.read``）。这一阶段没有测试点时 404。"""
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)
    await can.require(db, auth_user.user_id, ClaimPermission.read, membership)
    stage_at = stage_index(stage, await task_service.stage_keys(task_id))
    return ok(
        await _self_test_view(
            db, task_id=task_id, membership_id=membership.id, stage=stage_at
        )
    )


class TaskRejudgeOut(BaseModel):
    """重新评测排了多少：``queued`` 新排上的，``reused`` 同一份代码在这一版测试上
    已经评过、直接拿结果的，``unchanged`` 已经在这一版上评过或正在评的，``skipped``
    没记下代码版本或找不到项目的。"""

    queued: int
    reused: int
    unchanged: int
    skipped: int


@router.post(
    "/{taskId}/judge/rejudge",
    summary="Re-Judge Every Latest Submission",
    **typed_response(TaskRejudgeOut),
    dependencies=[Can(TaskPermission.teach)],
)
async def post_task_rejudge(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    stage: str | None = Query(default=None, max_length=16),
    task_service: TaskService = Depends(get_task_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """重新评测全部最新提交（方案 §改题后重新评测）：每个参与者在这一阶段最新的那一版，
    按现在这一版测试套再排一次。

    只给能批改的人（``Can(teach)``）。不给 ``stage`` 就是挂着测试点的每个阶段。评审不
    动：已经批过的那一版，分数照旧是评审里记的那个；没批过的那一版，老师批改时读到的
    就是新的评测结果。评测扣的算力照常记在空间所有者头上 —— 同一份代码在这一版测试上
    已经评过的不再跑。
    """
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if stage is None:
        stages = await test_suite.suite_stages(db, task_id)
    else:
        stages = [stage_index(stage, await task_service.stage_keys(task_id))]

    async def project_for(submission):
        # 之前没评测过的那一版（测试点是后来才加的）：按领题找回它的项目。
        membership = await membership_service.get_membership_by_id(
            submission.membership_id
        )
        project = await project_of_claim(
            db, task=task, membership=membership, user_id=submission.submitter_id
        )
        return project.id if project is not None else None

    totals = {"queued": 0, "reused": 0, "unchanged": 0, "skipped": 0}
    for stage_at in stages:
        counts = await judge_queue.rejudge_stage(
            db,
            task_id=task_id,
            stage=stage_at,
            requested_by=auth_user.user_id,
            project_for=project_for,
        )
        for key, value in counts.items():
            totals[key] += value
    await db.commit()
    return ok(totals)
