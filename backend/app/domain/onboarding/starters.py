"""The new-user starters: three 题目 on the platform's own 「新手题」 board.

A newcomer picks one on the welcome page (`frontend/src/views/welcome/`), and
taking it is taking an ordinary 题目: the claim starts at once, the project is
made with the requirements as its brief, the materials sit in its 资料库 under
「题目/」, the default teammate carries the pacing below, and the 题目's pack
pays for the run. Three things set these apart, all in the pack
(``resource_pack``, read by `app.domain.task.protocol`):

* ``model``: the project runs on DeepSeek V4.1 Flash unless it chose otherwise;
* ``once_per_person``: an account gets one starter's credits, however many
  starters it takes;
* ``compute_credits``: a placeholder until each starter has been run ten times
  and its average cost is known.

The board is made the first time anyone takes a starter, by 芝士 (the platform's
own agent account), and a 题目 that is missing is made again then. After that
the 题目 are ordinary data: people edit their text and materials on the board,
and nothing here overwrites them. `OnboardingStarter` rows are how the code
finds them again.

Who reads the board: the person taking a starter is added as a member first,
so the board, the 题目 page and the project's 「题目/」 folder read as they do
for any member.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.services import IdentityService
from app.domain.onboarding.models import OnboardingStarter
from app.domain.space.services import SpaceService
from app.domain.task import package
from app.domain.task.inputs import APPROVE_APPROVED, START_DIRECT, SUBMITTER_USER
from app.domain.task.models import Task
from app.domain.task.task_service import TaskService

StarterKey = Literal["web", "slides", "lesson"]

#: The model every starter runs on (decided 2026-11-08 by 马霄宇: a PPT starter
#: took about six minutes on it, with a layout on a par with the Claude run).
STARTER_MODEL: Final = "deepseek-flash"

#: Credits per starter. A placeholder: each starter is run ten times before
#: launch, and this becomes about twice the average cost.
STARTER_CREDITS: Final = 50

BOARD_NAME: Final = "新手题"
BOARD_INTRO: Final = "第一次用知是？领一道题，芝士带着你十分钟做完。"
BOARD_DESCRIPTION: Final = (
    "平台准备的三道新手题：活动主页网页、汇报 PPT、高中物理讲题网页。素材都已备好，"
    "什么都不用补就能做完；做的过程中会用到支线、选项按钮、任务页、预览和采纳。"
    "每道题由平台出一笔只在这个项目里用的额度，每个账号只发一次。"
)

#: The pacing every starter's teammate follows. It is what keeps a starter near
#: ten minutes and shows the newcomer the platform's pieces in order.
PACING: Final = """你在带一位第一次用知是的新用户做新手题。素材都在资料库的「题目/」文件夹里，题目要求就是项目目标。按下面的节奏做，不要多问：
1. 第一条回复：三行以内的计划，然后用选项按钮给两三套明显不同的风格让对方选（配色要互不相同，不要每套都是深蓝配橙）。不要反问素材里已经有的信息。
2. 对方选完，把这件事转成任务开工。
3. 做到一半，摆一次预览给对方看，问一句「哪里想改？」，用选项按钮给两三个改法。
4. 只接这一轮修改。改完直接递交付，告诉对方点「采纳」就收下了。
说话简短，用对方听得懂的话，不讲代码和技术细节。"""


@dataclass(frozen=True)
class StarterSpec:
    key: StarterKey
    name: str
    intro: str
    description: str
    #: Material files: (name, content).
    materials: tuple[tuple[str, str], ...]


_WEB_INFO = """# 校园技术分享会 · 活动信息

- 活动名称：「把想法做出来」校园技术分享会（第 3 期）
- 时间：2026 年 12 月 12 日（周六）14:00–17:30
- 地点：图书馆三层报告厅
- 主办：学生科技协会
- 人数：限 120 人，免费，需报名

## 嘉宾

1. 林知远：大四，计算机学院，分享「一个人做完一个小程序」
2. 周雨桐：研二，新闻学院，分享「用数据讲故事」
3. 陈墨：校友，独立开发者，分享「从课程作业到上线产品」

## 日程

| 时间 | 内容 |
| --- | --- |
| 14:00 | 签到、暖场 |
| 14:20 | 林知远：一个人做完一个小程序 |
| 15:00 | 周雨桐：用数据讲故事 |
| 15:40 | 茶歇、作品展示 |
| 16:10 | 陈墨：从课程作业到上线产品 |
| 16:50 | 圆桌问答 |
| 17:30 | 结束 |

## 报名

报名按钮跳到报名表（链接先用 # 占位），截止 12 月 10 日 24:00。
"""

_SLIDES_SUMMARY = """# 高一物理教研组 2026 年秋季学期工作总结

## 一、基本情况

本学期教研组共 8 位老师，承担高一年级 12 个班的物理教学，每周 4 课时。学期初制定了「以实验带概念」的教学目标，重点在力学部分增加学生动手实验的比例。

## 二、主要工作

1. 集体备课：每周三下午集体备课，全学期共 16 次，形成力学单元的统一课件和学案。
2. 实验教学：新开设 6 个学生分组实验（自由落体测重力加速度、探究加速度与力的关系等），实验开出率从上学期的 60% 提高到 95%。
3. 公开课与听评课：组内公开课 8 节，人均听课 22 节；2 位青年教师在区级评优课中获一等奖、二等奖各一项。
4. 分层作业：在 4 个班试行基础、提高两档作业，学生作业完成率从 82% 提高到 93%。

## 三、学生表现

期中、期末两次统考年级平均分分别为 71.5 和 76.2，及格率从 78% 提高到 86%。实验操作考核全年级通过率 97%。学生问卷中「喜欢物理课」的比例为 74%，比上学期提高 11 个百分点。

## 四、存在的问题

1. 部分班级两极分化明显，后 20% 的学生在受力分析上仍有困难。
2. 分组实验占用课时较多，进度比计划慢约一周。
3. 青年教师的命题能力还需要加强。

## 五、下学期计划

1. 把分层作业推广到全年级 12 个班。
2. 针对受力分析开设每周一次的答疑课。
3. 建设力学单元题库，每位老师负责一章，学期末完成 300 道题。
4. 组织 2 次命题研讨，由骨干教师带青年教师。
"""

_SLIDES_DATA = """项目,上学期,本学期
实验开出率(%),60,95
作业完成率(%),82,93
统考及格率(%),78,86
喜欢物理课的学生(%),63,74
人均听课节数,15,22
"""

_LESSON_PROBLEM = """# 平抛运动 · 题目与标准解法

## 题目

一个小球从高 h = 20 m 的平台边缘以 v₀ = 10 m/s 的速度水平抛出，不计空气阻力，取 g = 10 m/s²。求：

1. 小球落地所用的时间 t；
2. 小球落地点到平台边缘的水平距离 x；
3. 小球落地时速度的大小和方向。

## 标准解法

平抛运动可以分解为水平方向的匀速直线运动和竖直方向的自由落体运动，两个方向互不影响，用同一个时间 t 联系。

1. 竖直方向：h = ½gt²，所以 t = √(2h/g) = √(2×20/10) s = 2 s。
2. 水平方向：x = v₀t = 10 × 2 m = 20 m。
3. 落地时竖直分速度 v_y = gt = 20 m/s，水平分速度仍为 v₀ = 10 m/s。
   合速度 v = √(v₀² + v_y²) = √(100 + 400) m/s = 10√5 m/s ≈ 22.4 m/s；
   与水平方向夹角 θ 满足 tan θ = v_y / v₀ = 2，θ ≈ 63.4°。

## 要点

- 落地时间只由高度决定，与初速度无关。
- 水平距离由高度和初速度共同决定。
"""

STARTERS: Final[tuple[StarterSpec, ...]] = (
    StarterSpec(
        key="web",
        name="新手题：做一个活动主页",
        intro="给一场校园技术分享会做一页活动主页：时间地点、嘉宾、日程和报名按钮。",
        description="""## 要做什么

照资料库「题目/活动信息.md」给「把想法做出来」校园技术分享会做一页式活动主页。

## 要求

- 只做一页网页，不接后端；报名按钮链接用 # 占位。
- 包含：活动名称和一句话介绍、时间地点、三位嘉宾、日程表、报名按钮。
- 手机和电脑上都要好看，打开就能看，不需要懂代码。
- 先给两三套明显不同的风格让我选，配色不要都是深蓝配橙。

## 换成自己的

做完以后，可以把活动信息换成你自己的活动或作品集，这道题就是模板。""",
        materials=(("活动信息.md", _WEB_INFO),),
    ),
    StarterSpec(
        key="slides",
        name="新手题：把学期总结做成汇报 PPT",
        intro="把教研组的学期工作总结做成一份 8 页左右、版式统一的汇报 PPT。",
        description="""## 要做什么

把资料库「题目/学期工作总结.md」做成一份汇报 PPT（.pptx），数据用「题目/数据对比.csv」做一张图表。

## 要求

- 8 页左右，不超过 10 页：封面、目录、正文、结尾。
- 版式统一；正文字号不小于 18 磅，标题不小于 28 磅。
- 章节编号和目录一致，目录里写的每一节都要有对应的页。
- 至少一页用图表展示本学期和上学期的数据对比。
- 先给两三套明显不同的风格让我选：配色要互不相同，不要每套都是深蓝配橙。

## 换成自己的

做完以后，可以把材料换成你自己的总结、报告或课件，这道题就是模板。""",
        materials=(
            ("学期工作总结.md", _SLIDES_SUMMARY),
            ("数据对比.csv", _SLIDES_DATA),
        ),
    ),
    StarterSpec(
        key="lesson",
        name="新手题：平抛运动互动讲题",
        intro="做一页平抛运动的互动讲题网页：分步讲解、能拖参数的轨迹动画和 3 道变式题。",
        description="""## 要做什么

照资料库「题目/题目与标准解法.md」做一页讲题网页，把这道平抛运动题讲清楚。

## 要求

- 只讲这一道题，一页网页，不接后端。
- 分步讲解：先拆成水平和竖直两个方向，再一步步算出时间、水平距离和落地速度。
- 一个能拖动的动画：拖初速度和高度，看轨迹和落点怎么变，同时显示算出的时间和距离。
- 最后出 3 道变式题，每道可以点开看答案和简要解析。
- 公式和数字要算对；手机上也要能看、能拖。
- 先给两三套明显不同的风格让我选。

## 换成自己的

做完以后，可以拍照上传你自己的题，让芝士照这个样子再讲一道。""",
        materials=(("题目与标准解法.md", _LESSON_PROBLEM),),
    ),
)
_BY_KEY: Final = {spec.key: spec for spec in STARTERS}

#: Serialises making the board: two first takers at once make it once.
_LOCK_KEY: Final = "onboarding-starters"


def _pack() -> dict:
    return {
        "compute_credits": STARTER_CREDITS,
        "prompt": PACING,
        "model": STARTER_MODEL,
        "once_per_person": True,
    }


async def starter_task(session: AsyncSession, key: StarterKey) -> Task:
    """The 题目 behind this starter, making the board or the 题目 if missing.

    Made under a transaction-scoped advisory lock, so two first takers make it
    once. A starter whose 题目 was deleted is made again on the same board."""
    found = await _live_task(session, key)
    if found is not None:
        return found
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _LOCK_KEY}
    )
    found = await _live_task(session, key)
    if found is not None:
        return found
    author = await IdentityService(session).ensure_agent_user()
    space_id, category_id = await _board(session, author_id=author.id)
    task = await _make_task(
        session,
        _BY_KEY[key],
        space_id=space_id,
        category_id=category_id,
        author_id=author.id,
    )
    row = await session.get(OnboardingStarter, key)
    if row is None:
        session.add(
            OnboardingStarter(
                key=key,
                space_id=space_id,
                task_id=task.id,
                created_at=datetime.now(UTC),
            )
        )
    else:
        # Stamped again: the newest row names the board starters are made on.
        row.space_id, row.task_id = space_id, task.id
        row.created_at = datetime.now(UTC)
    await session.flush()
    return task


async def seat_on_board(session: AsyncSession, task: Task, user_id: int) -> None:
    """Make the taker a member of the starters' board, so the 题目 and its
    materials read for them as for any member. Idempotent."""
    await SpaceService.for_routes(session).add_member(
        space_id=task.space_id, target_user_id=user_id
    )


async def _live_task(session: AsyncSession, key: StarterKey) -> Task | None:
    row = await session.get(OnboardingStarter, key)
    if row is None:
        return None
    task = await session.get(Task, row.task_id)
    if task is None or task.deleted_at is not None:
        return None
    # Deleting a board leaves its 题目 alive; a starter on a deleted board is
    # missing too, and is made again on a new one.
    if await SpaceService.for_routes(session).get_space(task.space_id) is None:
        return None
    return task


async def _board(session: AsyncSession, *, author_id: int) -> tuple[int, int]:
    """The starters' board and its 分组: a live board an earlier starter is on,
    or a new one. Every board the starters were ever on is asked, newest first:
    after a board is deleted, the first starter remade moves its row to a new
    board while the others still point at the deleted one, and the next starter
    remade must land on that new board, not make a third."""
    spaces = SpaceService.for_routes(session)
    space = None
    for space_id in await session.scalars(
        select(OnboardingStarter.space_id)
        .group_by(OnboardingStarter.space_id)
        .order_by(func.max(OnboardingStarter.created_at).desc())
    ):
        space = await spaces.get_space(space_id)
        if space is not None:
            break
    if space is None:
        # 芝士 owns it, so the board has an owner like any other.
        space = await spaces.create_space(
            name=BOARD_NAME,
            intro=BOARD_INTRO,
            description=BOARD_DESCRIPTION,
            avatar_id=None,
            enable_rank=False,
            owner_id=author_id,
            task_templates=[],
        )
    assert space.default_category_id is not None  # create_space always sets it
    return space.id, space.default_category_id


async def _make_task(
    session: AsyncSession,
    spec: StarterSpec,
    *,
    space_id: int,
    category_id: int,
    author_id: int,
) -> Task:
    task = await TaskService.of(session).create_task(
        name=spec.name,
        intro=spec.intro,
        description=spec.description,
        creator_id=author_id,
        space_id=space_id,
        category_id=category_id,
        submitter_type=SUBMITTER_USER,
        deadline=None,
        registration_start_at=None,
        participant_limit=None,
        default_deadline=30,
        resubmittable=True,
        editable=True,
        rank=None,
        require_real_name=False,
        min_team_size=None,
        max_team_size=None,
        team_locking_policy="NO_LOCK",
        start_mode=START_DIRECT,
    )
    now = datetime.now(UTC)
    task.protocol_override = {"resource_pack": _pack()}
    task.approved = APPROVE_APPROVED
    task.published_at = now
    task.reviewed_at = now
    for filename, content in spec.materials:
        await package.add_file(
            session, task, by=author_id, filename=filename, data=content.encode()
        )
    await package.record(session, task, by=author_id, changes={package.PUBLISHED: True})
    return task
