"""把一台开发库灌到真实规模，好让反馈中心与后台真的被量过 (seed feedback volume).

**这是一个测量工具，不是一个生产工具。** 反馈中心的性能现在全靠猜：列表在 800 条
的时候还顺不顺、楼中楼分页在第 150 条回复上会不会抖、`EXPLAIN` 到底走不走得上
`ix_feedback_visibility_status_created` —— 这些问题在一条只有三条 demo 数据的库上
问不出来，只能靠人真的在浏览器里滚一遍、真的看一眼执行计划。所以这个脚本造的不是
「好看的样例」，是**规模**：形状对，数量对，密度对。

## 造出来的形状

* `--feedback` 条反馈，`created_at` 铺在最近 `--days` 天里，**近 30 天明显更密** ——
  真实产品的反馈是长出来的，不是均匀撒的，而「最近一个月」正好是列表默认那一段。
  状态也跟着年龄走：新的多在 `received`/`in_progress`，老的才走到 `resolved`/
  `deployed`。这样「办完了的 bug 沉底」那条规则才有东西可沉。
* `title` 从两段小词表里组合（`反馈列表 点了没反应`），**故意会重名** ——
  搜索框里打「卡顿」能出一片、打全称能看出重名之间怎么分，都是有东西可看的。
  `summary`/`problem` 是 100～600 字的中文正文：滚动和排版要压的就是这些字。
* `security=True` 的行**一律是 `private`**。模型里 `security` 是 `private` 的细化，
  不是第二个开关（`services.visible_to` 读的就是这个意思），造一条「公开但安全」的
  行等于造一条这个产品认为不存在的行。
* agent 提的反馈（约 15%）走**提案那条路**的字段形状：`author_handle` 是 agent，
  `submitted_by_handle` 是按发送的那个人，`author_user_id` 为 NULL。服务端只允许这
  一个形状（`services.create` 拒掉 agent 直接发布），造别的形状就是在测一个不会出现
  的数据集。只有 agent 行才填 `what_happened`/`repro`/`evidence` —— 模型说这三个
  字段「only an agent can fill honestly」。
* 支持按长尾给：约三分之一一条都没有，多数 1～3 个，少数几十个。`created_at` 在反馈
  之后、且**靠近反馈创建时间更密**（`u ** 2`），因为「热门」是随时间衰减的。
* 评论：一部分反馈才有；`--long-thread` 条反馈各带 80～200 条回复 —— 楼中楼分页和
  折叠要压的就是这几栋。**两层、且折回同一栋**：`parent_id` 永远指顶层评论，回复的
  回复挂到自己的爷爷上；`reply_to_handle` 只在「被回复的那条本身是回复」时才有值，
  其余 NULL。这是 `models.FeedbackComment` 类说明里写死的那条规则，脚本照着它造，不
  造这个模型认为不可能存在的形状。少量评论带 `deleted_at`（软删除），楼里那条
  「回复 (已删除)」的渲染路径才有东西可渲染。
* 每条反馈的状态**同时**写一行 `feedback_timeline`。模型说这两份拷贝「必须一起写」
  （`FeedbackTimeline` 的类说明），只写其中一份就是一条这个库自己不会产生的数据。
* 作者池 `--authors` 个人，每个人都有**真的 `user` + `user_profile` 行**，昵称带中文，
  这样头像那条 join、「按作者搜」、后台按人筛都有真实对象，而不是一堆悬空 handle。

## 前缀与清理

所有由本脚本造出来的 handle / username / 邮箱共用同一个前缀（`--prefix`，默认
`volseed`）：`volseed-01` … `volseed-60`、`volseed-agent-01`、
`volseed-01@example.invalid`。
**判据只有前缀**，没有「最近创建的」这类判据 —— 那会误伤同一台库上别人的数据。
`--purge --apply` 按依赖顺序删回去（点赞 → 支持 → 评论 → 时间线 → 反馈 →
user_profile → user），可重复跑，只会删带前缀的。

为了这个，agent 的 handle 是 `volseed-agent-NN` 而不是 `cheese-` 命名空间里的真
handle：这个脚本只跑在开发库上，一个前缀能一次清干净比命名空间正确更重要。（代价是
`looks_like_agent_handle()` 那条「看名字猜」的展示规则认不出它们；列表和详情读的是
`author_is_agent` 这一列，agent 徽标照样出得来。）

## 幂等与随机

`--seed`（默认 20260921）固定全部随机性：两次跑出来的形状一致，前后两张截图才比得
下去。`--apply` 连跑两次不会炸（唯一约束那两张表先按生成名单去过重、再跟库里已有的
比一遍，username 已存在就复用那些 user 行），第二次会**多出一批新行** —— 这是造数
工具，可以接受。

    uv run python -m scripts.seed_feedback_volume                  # dry run
    uv run python -m scripts.seed_feedback_volume --apply
    uv run python -m scripts.seed_feedback_volume --purge --apply   # 清干净

用 `python -m` 而不是 `python scripts/...`：直接跑那个路径时 `sys.path[0]` 是
`scripts/`，`import app` 找不到包（除非自己带上 `PYTHONPATH=.`）。库里几个老脚本的
用法行写的是后者 —— 它们现在是跑不起来的，别照抄。

⚠️ 只该跑在开发/测试库上 —— 这句话现在是**代码**在管，不再只是这一行字：加 `--apply`
之前会先看目标库叫什么都不叫，名字里没有 `test`/`dev`/`e2e`/`seed`/`scratch` 就直接
拒绝并打印实际连接串（`require_seedable_target`）。它会往库里加几万行，而且默认那
800 条是**追加**的。`--allow-any-database` 能松掉这一关，但那一下得你自己按。
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401 — 所有表都注册到 Base.metadata，FK 才解析得出来
from app.core.db import async_session_factory, engine
from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackCommentLike,
    FeedbackKind,
    FeedbackPriority,
    FeedbackStatus,
    FeedbackSupport,
    FeedbackTimeline,
    FeedbackVisibility,
)
from app.domain.user.models import User, UserProfile

#: 状态阶梯。`feedback_timeline` 按这个顺序补，一条反馈走到第 N 级就有 N 行。
LADDER: tuple[FeedbackStatus, ...] = (
    FeedbackStatus.received,
    FeedbackStatus.in_progress,
    FeedbackStatus.resolved,
    FeedbackStatus.deployed,
)

#: 近 30 天是列表默认看的那一段，也是「更密」的那一段。
_RECENT_DAYS = 30
_RECENT_SHARE = 0.6
#: 一条反馈的正文长度区间（字符）。这是要压滚动和排版的东西，不能是「测试测试」。
_BODY_MIN, _BODY_MAX = 100, 600
#: 每次 flush 的行数。一条一 commit 在这里是纯粹的自杀，批太大又把内存吃满。
_BATCH = 1000
#: agent 提的反馈占比，以及 agent 作者在作者池里的比例。
_AGENT_SHARE = 0.15
_AGENT_RATIO = 10

_TITLE_SUBJECTS = (
    "反馈列表",
    "话题侧边栏",
    "文档编辑器",
    "通知铃铛",
    "成员管理页",
    "全局搜索",
    "登录页",
    "附件上传",
    "移动端布局",
    "后台审核台",
    "消息时间线",
    "项目设置",
    "记忆面板",
    "看板视图",
    "数据导出",
    "快捷键",
)
_TITLE_SYMPTOMS = (
    "点了没反应",
    "滚动时卡顿",
    "分页出现重复",
    "样式错位",
    "偶发 500",
    "数据不刷新",
    "加载很慢",
    "文字被截断",
    "排序不对",
    "重复提交",
)

_STEPS = (
    "打开页面后等首屏渲染完成",
    "把窗口宽度拖到 1280 以下再拖回来",
    "连续快速点击三次刷新",
    "先切到另一个标签页，半分钟后再切回来",
    "在网络面板限速到 Slow 3G 之后重试",
    "用同一个账号在两个浏览器里同时打开这一页",
)
_EXPECTED = (
    "列表应当保持滚动位置不变",
    "两边的数据应当一致",
    "请求应当只发一次",
    "页面应当立刻给出反馈，而不是先空白两秒",
    "切回来时应当补上这段时间的新消息",
    "排序应当稳定，同一批数据每次结果一致",
)
_ACTUAL = (
    "滚动位置被重置到顶部，得重新找回刚才那一条",
    "右边多出一条重复的卡片，刷新之后才消失",
    "控制台里能看到同一个接口连发了三次，其中两次被取消",
    "整页白了大约两秒，之后才慢慢把内容画出来",
    "新消息要手动刷新才出现，铃铛上的数字却已经变了",
    "同样的数据两次打开顺序不同，截图对不上",
)
_IMPACT = (
    "一天要点十几次，每次都要重新找位置，效率掉得厉害",
    "新同事第一次用就问了三次，说明这个位置确实反直觉",
    "影响面不大，但每次看到都会怀疑自己是不是点错了",
    "在评审会上演示的时候正好撞上，比较尴尬",
    "数据本身没错，只是看起来像错了，解释成本很高",
)
_EXTRA = (
    "环境：macOS 15 + Chrome 131，分辨率 2560x1440，缩放 125%",
    "复现概率大约三次里有一次，和网络快慢看不出关系",
    "同一台机器上用 Safari 试过，Safari 上是正常的",
    "公司内网和家里宽带都试过，两边都能复现",
    "没有开任何插件，用无痕窗口也一样",
    "后端日志里这段时间没有报错，只有慢查询的告警",
    "这个问题上周还没有，升级之后才出现",
    "临时绕过办法是先刷新一次，但每次都这样很难受",
)
_AGENT_WHAT_HAPPENED = (
    "我在这个话题里连续发了四次消息，前三次都返回成功，话题时间线里却只有最后一条。"
    "第四次之后事件行的 `meta.turn_id` 和上一条相同，说明三次写进了同一个 turn。",
    "跑集成测试时，第三个用例开始连接被拒，报错是 "
    "`asyncpg.exceptions.TooManyConnectionsError`，数据库那边 `pg_stat_activity` "
    "里有十一条 `idle in transaction`，而测试进程自己只开了一条。",
    "用户点了「保存到资料库」，界面显示成功，资料库里却没有这份文件；接口在响应里"
    "回的是空数组，状态码还是 200。",
)
_AGENT_REPRO = (
    "1. `cheese chat send --topic <id> --content ping` 连发三次\n"
    "2. 打开话题时间线\n"
    "3. 只看到最后一条，前两条的 `turn_id` 与它相同",
    "1. `CHEESE_CI_SLOT=x1 uv run pytest tests/integration -q`\n"
    "2. 等到第三个用例\n"
    "3. 连接被拒，`pg_stat_activity` 里是 `idle in transaction`",
    "1. 打开一个话题，切到文件面板\n"
    "2. 上传任意文件并点「保存到资料库」\n"
    "3. `GET /library` 返回空数组",
)
_AGENT_EVIDENCE = (
    "话题的 `events` 表里这三条 `turn_id` 完全相同，`blocks` 表里只有一条；"
    "话题日志里能看到三次请求都是 200。",
    "复现三次中了三次。数据库连接数上限 20，测试进程自己只用一条。",
    '接口返回体是 `{"data": [], "total": 0}`，同时 `attachments` 表里那一行还在，'
    "`scope` 是 `library`。",
)
_LOG_SAMPLE = (
    "2026-09-18 10:12:03 INFO  request  POST /feedback 200 41ms\n"
    "2026-09-18 10:12:04 WARN  db       pool checked_out=19/20 waited=1.8s\n"
    "2026-09-18 10:12:04 INFO  request  GET /feedback 200 2103ms\n"
    "2026-09-18 10:12:05 ERROR db       statement timeout after 5.0s\n"
    "  query: SELECT feedback.* FROM feedback WHERE visibility='public'\n"
    "         ORDER BY created_at DESC LIMIT 20 OFFSET 4000\n"
)

_COMMENT_BODIES = (
    "我也遇到了，而且不是偶发，是稳定复现。",
    "补充一个信息：在窄窗口下也会出现，不限于移动端。",
    "这个和上周那条「分页出现重复」应该是同一个根因，建议一起看。",
    "临时绕过：先刷新一次就好了，但不能每次都这样。",
    "按你说的试了一遍，确实好了，谢谢。",
    "我这边复现不出来，可能是数据量上的差别？我这边只有几十条。",
    "同意上面说的，这个位置确实反直觉，第一次用的时候我也找了一会儿。",
    "已经修了，等下一次发版。",
    "这个不是 bug，是设计上就这样的，之前讨论过。",
    "截图我贴在附件里了，第 3 张能看清控制台的报错。",
    "优先级建议提一下，演示的时候容易撞上。",
    "同样的现象在 Safari 上没有，只有 Chrome 有。",
    "我们这边三个人都能复现，不是环境问题。",
    "感谢反馈，已经记下来了，排到这一批里。",
    "这条和另一起问题连在一起看会清楚一些。",
)
_AGENT_COMMENT_BODIES = (
    "我看了一下，这条和「分页出现重复」是同一处偏移，已经合到一处改。",
    "已复现。根因是列表请求没有去重，等修完我在这条下面回。",
    "补充证据：接口在这段时间里被调用了三次，其中两次的结果完全一样。",
)

_NICKNAMES = (
    "林晓",
    "王雨桐",
    "陈子昂",
    "刘一诺",
    "赵思远",
    "周思齐",
    "吴可",
    "郑亦航",
    "孙泽宇",
    "何知微",
    "芝士小助手",
    "匿名用户",
)
_INTRO = "这个账号是造数脚本建出来的，用来压反馈中心的规模。"


# ---------------------------------------------------------------------------
# 参数与数据形状
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """一次造数的全部输入。纯数据，测试可以用一个小号 Spec 直接调 `build()`。"""

    feedback: int = 800
    days: int = 180
    authors: int = 60
    prefix: str = "volseed"
    seed: int = 20260921
    long_thread: int = 3
    comment_share: float = 0.85

    @property
    def agents(self) -> int:
        """agent 作者的个数。按作者池的十分之一算，至少一个。"""
        return max(1, self.authors // _AGENT_RATIO)

    @property
    def human_handles(self) -> list[str]:
        return [f"{self.prefix}-{i:02d}" for i in range(1, self.authors + 1)]

    @property
    def agent_handles(self) -> list[str]:
        return [f"{self.prefix}-agent-{i:02d}" for i in range(1, self.agents + 1)]

    @property
    def all_handles(self) -> list[str]:
        return self.human_handles + self.agent_handles


@dataclass
class Dataset:
    """要写库的全部行。生成是纯的，写库是另一件事 —— 这样测试能只看形状。"""

    users: list[User] = field(default_factory=list)
    feedback: list[Feedback] = field(default_factory=list)
    supports: list[FeedbackSupport] = field(default_factory=list)
    comments: list[FeedbackComment] = field(default_factory=list)
    likes: list[FeedbackCommentLike] = field(default_factory=list)
    timeline: list[FeedbackTimeline] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "user": len(self.users),
            "feedback": len(self.feedback),
            "feedback_supports": len(self.supports),
            "feedback_comments": len(self.comments),
            "feedback_comment_likes": len(self.likes),
            "feedback_timeline": len(self.timeline),
        }


# ---------------------------------------------------------------------------
# 生成（纯函数，不碰 session）
# ---------------------------------------------------------------------------


def _body(rng: random.Random, scenario: str, symptom: str) -> str:
    """一条 100～600 字的中文正文。长度本身就是被测对象，所以这里要真的凑够。"""
    steps = "，然后".join(rng.sample(_STEPS, rng.randint(2, 3)))
    parts = [
        f"我在「{scenario}」上遇到一个问题：{symptom}。",
        f"复现步骤：{steps}。",
        f"预期：{rng.choice(_EXPECTED)}。",
        f"实际：{rng.choice(_ACTUAL)}。",
        f"影响：{rng.choice(_IMPACT)}。",
    ]
    while len("".join(parts)) < _BODY_MIN:
        parts.append(rng.choice(_EXTRA) + "。")
    # 再补几条，正文长度才会落满 100～600 这一整段 —— 全都在 120 字上下的话，
    # 「长正文会不会把列表撑坏」这件事就没被压到。
    for _ in range(rng.randint(0, 4)):
        parts.append(rng.choice(_EXTRA) + "。")
    return "".join(parts)[:_BODY_MAX]


def _summary(problem: str) -> str:
    """列表那一行。原型取正文前 60 字（见 `Feedback.summary` 的列说明）。"""
    return problem if len(problem) <= 60 else problem[:57] + "…"


def _created_at(rng: random.Random, now: datetime, days: int) -> datetime:
    """反馈的创建时间：近 30 天更密，剩下的铺在那之前。"""
    recent = min(_RECENT_DAYS, days)
    if rng.random() < _RECENT_SHARE:
        ago = rng.uniform(0, recent)
    elif days > _RECENT_DAYS:
        ago = rng.uniform(_RECENT_DAYS, days)
    else:
        ago = rng.uniform(0, days)
    return now - timedelta(days=ago)


def _times_between(
    rng: random.Random, base: datetime, now: datetime, count: int, *, skew: float = 1.0
) -> list[datetime]:
    """`count` 个递增的时间点，都在 `base` 之后、`now` 之前。

    递增是必需的：回复不能早于它回的那条，支持也不该早于反馈本身。`skew > 1` 把点
    往 `base` 那一头压 —— 支持数和评论数的时间衰减要的就是这个形状，热门的东西在
    发布后不久就该有人响应。
    """
    span = max((now - base).total_seconds(), 60.0)
    return sorted(
        base + timedelta(seconds=span * rng.random() ** skew) for _ in range(count)
    )


def _status(rng: random.Random, age_days: float) -> FeedbackStatus:
    """状态跟着年龄走：新的还在收/处理，老的才走到修复/上线。"""
    weights = (45, 35, 15, 5) if age_days < _RECENT_DAYS else (10, 20, 40, 30)
    return rng.choices(LADDER, weights=weights, k=1)[0]


def _support_count(rng: random.Random, pool: int) -> int:
    """长尾：约三分之一 0 个，多数 1～3 个，少数几十个。"""
    roll = rng.random()
    if roll < 0.35:
        return 0
    if roll < 0.75:
        return rng.randint(1, 3)
    if roll < 0.93:
        return rng.randint(4, 10)
    return min(pool, rng.randint(11, 40))


def _comment_count(rng: random.Random, long_thread: bool) -> int:
    """一条反馈下面挂多少评论。长楼那几条单独给 80～200。"""
    return rng.randint(80, 200) if long_thread else rng.randint(3, 27)


def _human(rng: random.Random, handles: Sequence[str]) -> str:
    return rng.choice(handles)


def _nickname(handle: str) -> str:
    """从 handle 定出来的昵称。用 `hash()` 是不行的：它带进程级随机盐。"""
    return f"{_NICKNAMES[sum(map(ord, handle)) % len(_NICKNAMES)]}-{handle[-2:]}"


def _user(handle: str, now: datetime, days: int) -> User:
    joined = now - timedelta(days=days)
    return User(
        username=handle,
        email=f"{handle}@example.invalid",
        email_domain="example.invalid",
        created_at=joined,
        updated_at=joined,
    )


def build(spec: Spec, now: datetime | None = None) -> Dataset:
    """把 spec 变成一个 `Dataset`。同 spec 同结果（`--seed` 固定了全部随机性）。"""
    rng = random.Random(spec.seed)
    now = now or datetime.now(UTC)
    dataset = Dataset()

    for handle in spec.all_handles:
        dataset.users.append(_user(handle, now, spec.days))

    humans = spec.human_handles
    agents = spec.agent_handles
    # 长楼那几条：均匀挑，别全挤在最近那一批里 —— 分页要在冷热两种数据上都压过。
    step = max(1, spec.feedback // max(1, spec.long_thread))
    long_ids = {i * step for i in range(spec.long_thread) if i * step < spec.feedback}

    for index in range(spec.feedback):
        thread_it = index in long_ids
        row = _build_feedback(
            rng,
            spec,
            long_thread=thread_it,
            humans=humans,
            agents=agents,
            now=now,
        )
        dataset.feedback.append(row)
        dataset.timeline.extend(_timeline_for(rng, row, now, humans))
        dataset.supports.extend(_supports_for(rng, row, now, humans))
        # 长楼那几条一定进；其余的按 --comment-share 决定有没有评论。
        if thread_it or rng.random() < spec.comment_share:
            comments, likes = _thread_for(
                rng,
                feedback_id=row.id,
                humans=humans,
                agents=agents,
                created=row.created_at,
                now=now,
                long_thread=thread_it,
            )
            dataset.comments.extend(comments)
            dataset.likes.extend(likes)

    return dataset


def _build_feedback(
    rng: random.Random,
    spec: Spec,
    *,
    long_thread: bool,
    humans: Sequence[str],
    agents: Sequence[str],
    now: datetime,
) -> Feedback:
    created = _created_at(rng, now, spec.days)
    age_days = (now - created).total_seconds() / 86400
    scenario = rng.choice(_TITLE_SUBJECTS)
    symptom = rng.choice(_TITLE_SYMPTOMS)
    problem = _body(rng, scenario, symptom)
    security = rng.random() < 0.02
    is_agent = rng.random() < _AGENT_SHARE
    feedback_id = uuid.uuid4()

    row = Feedback(
        id=feedback_id,
        title=f"{scenario} {symptom}",
        summary=_summary(problem),
        kind=rng.choices(list(FeedbackKind), weights=(55, 30, 15), k=1)[0],
        status=_status(rng, age_days),
        # security 是 private 的细化，不是第二个开关：安全条目一律私密。
        visibility=(
            FeedbackVisibility.private
            if security
            else rng.choices(list(FeedbackVisibility), weights=(78, 22), k=1)[0]
        ),
        priority=rng.choices(list(FeedbackPriority), weights=(20, 55, 20, 5), k=1)[0],
        security=security,
        problem=problem,
        why=rng.choice(_IMPACT) if rng.random() < 0.5 else None,
        expectation=rng.choice(_EXPECTED) if rng.random() < 0.7 else None,
        # 只有 agent 能诚实填的三段。
        what_happened=rng.choice(_AGENT_WHAT_HAPPENED) if is_agent else None,
        repro=rng.choice(_AGENT_REPRO) if is_agent else None,
        evidence=rng.choice(_AGENT_EVIDENCE) if is_agent else None,
        logs=_LOG_SAMPLE * 3 if rng.random() < 0.05 else None,
        session_id=uuid.uuid4().hex[:16] if is_agent else None,
        environment="macOS 15 / Chrome 131" if rng.random() < 0.4 else None,
        author_handle=rng.choice(agents) if is_agent else _human(rng, humans),
        author_user_id=None,
        author_is_agent=is_agent,
        # 提案那条路：作者是 agent，按发送的是人。人直接提的那条两个字段都空。
        submitted_by_handle=_human(rng, humans) if is_agent else None,
        submitted_by_user_id=None,
        assignee_handle=_human(rng, humans) if rng.random() < 0.35 else None,
        tags=rng.sample(
            ["性能", "UI", "文档", "移动端", "agent", "搜索"], rng.randint(0, 2)
        ),
        created_at=created,
        updated_at=created,
    )
    return row


def _timeline_for(
    rng: random.Random, row: Feedback, now: datetime, humans: Sequence[str]
) -> list[FeedbackTimeline]:
    """状态走到第几级就有几行。两级之间用递增的时间点，第一行就是反馈创建时刻。"""
    reached = LADDER.index(row.status)
    marks = _times_between(rng, row.created_at, now, reached + 1)
    marks[0] = row.created_at
    return [
        FeedbackTimeline(
            id=uuid.uuid4(),
            feedback_id=row.id,
            status=rung,
            at=at,
            by_handle=row.assignee_handle or _human(rng, humans),
        )
        for rung, at in zip(LADDER, marks, strict=False)
    ]


def _supports_for(
    rng: random.Random, row: Feedback, now: datetime, humans: Sequence[str]
) -> list[FeedbackSupport]:
    """一个 (反馈, 作者) 只有一行 —— 取样的作者不重复，唯一约束就撞不上。"""
    count = min(_support_count(rng, len(humans)), len(humans))
    authors = rng.sample(humans, count)
    marks = _times_between(rng, row.created_at, now, count, skew=2.0)
    return [
        FeedbackSupport(
            id=uuid.uuid4(),
            feedback_id=row.id,
            author_handle=author,
            created_at=at,
        )
        for author, at in zip(authors, marks, strict=True)
    ]


def _comment_body(rng: random.Random, is_agent: bool) -> str:
    pool = _AGENT_COMMENT_BODIES if is_agent else _COMMENT_BODIES
    return rng.choice(pool)


def _thread_for(
    rng: random.Random,
    *,
    feedback_id: uuid.UUID,
    humans: Sequence[str],
    agents: Sequence[str],
    created: datetime,
    now: datetime,
    long_thread: bool,
) -> tuple[list[FeedbackComment], list[FeedbackCommentLike]]:
    """一条反馈下面的评论与点赞。

    两层、折回同一栋：每条回复的 `parent_id` 都是某条**顶层**评论，回复的回复也挂在
    同一个顶层评论下（`parent?.parentId ?? parent?.id`）。`reply_to_handle` 只在被回复
    的那条本身是回复时才写 —— 回顶层的和顶层一样渲染，写了等于每一条都写。
    """
    total = _comment_count(rng, long_thread)
    # 顶层评论占四分之一到一半，其余都是楼内回复。
    top_count = max(1, total // rng.randint(2, 4))
    marks = _times_between(rng, created, now, total)
    cursor = 0
    thread: list[FeedbackComment] = []
    tops: list[FeedbackComment] = []
    #: 每栋楼里已经存在的回复，回复的回复从这里挑目标。
    replies: dict[uuid.UUID, list[FeedbackComment]] = {}

    def _author() -> tuple[str, bool]:
        if rng.random() < 0.08:
            return rng.choice(agents), True
        return _human(rng, humans), False

    def _add(parent_id: uuid.UUID | None, reply_to: str | None) -> FeedbackComment:
        nonlocal cursor
        author, is_agent = _author()
        row = FeedbackComment(
            id=uuid.uuid4(),
            feedback_id=feedback_id,
            parent_id=parent_id,
            author_handle=author,
            author_user_id=None,
            author_is_agent=is_agent,
            body=_comment_body(rng, is_agent),
            reply_to_handle=reply_to,
            created_at=marks[cursor],
        )
        cursor += 1
        # 少量软删除：楼里那条「回复 (已删除)」的渲染路径要有东西可渲染。
        if rng.random() < 0.02:
            row.deleted_at = row.created_at + timedelta(hours=rng.randint(1, 48))
        thread.append(row)
        return row

    for _ in range(top_count):
        # 顶层评论没有回复对象，`reply_to_handle` 恒为 NULL。
        top = _add(None, None)
        tops.append(top)
        replies[top.id] = []

    for _ in range(total - top_count):
        top = rng.choice(tops)
        pool = replies[top.id]
        # 四成回复是在回某条回复 —— 这时才写 `reply_to_handle`，而 `parent_id` 仍然
        # 折回顶层那一条，不是被回复的那条回复。
        target = rng.choice(pool) if pool and rng.random() < 0.4 else None
        reply = _add(top.id, target.author_handle if target is not None else None)
        replies[top.id].append(reply)

    likes: list[FeedbackCommentLike] = []
    like_pool = list(humans) + list(agents)
    for row in thread:
        if rng.random() >= 0.3:
            continue
        for author, at in zip(
            rng.sample(like_pool, min(rng.randint(1, 6), len(like_pool))),
            _times_between(rng, row.created_at, now, 6, skew=2.0),
            strict=False,
        ):
            likes.append(
                FeedbackCommentLike(
                    id=uuid.uuid4(),
                    comment_id=row.id,
                    author_handle=author,
                    created_at=at,
                )
            )
    return thread, likes


# ---------------------------------------------------------------------------
# 写库
# ---------------------------------------------------------------------------


def _batched(rows: Sequence, size: int = _BATCH) -> Iterable[Sequence]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _dedupe(rows: Sequence, *keys: str) -> list:
    """按 keys 去重，保留第一条。生成的名单理论上已经不会撞，但唯一约束上一颗雷就是
    重复的 (feedback, author) —— 第二次跑（库里已经有行）时它就是真的。"""
    seen: set[tuple] = set()
    kept = []
    for row in rows:
        key = tuple(getattr(row, name) for name in keys)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


async def _existing_users(session: AsyncSession, prefix: str) -> dict[str, int]:
    """已经存在的前缀用户名 → user id。第二次跑就复用它们，不重复插入用户。"""
    rows = (
        await session.execute(
            select(User.username, User.id).where(User.username.like(f"{prefix}%"))
        )
    ).all()
    return {username: user_id for username, user_id in rows}


async def _known_pairs(session: AsyncSession, one, two) -> set[tuple]:
    return {(a, b) for a, b in (await session.execute(select(one, two))).all()}


async def apply_dataset(
    session: AsyncSession, dataset: Dataset, spec: Spec, *, progress: bool = True
) -> dict[str, int]:
    """分批写进去，最后 commit 一次。第二次跑不会炸。"""
    existing = await _existing_users(session, spec.prefix)
    fresh_users = [u for u in dataset.users if u.username not in existing]
    for chunk in _batched(fresh_users):
        session.add_all(chunk)
        await session.flush()

    ids = dict(existing)
    ids.update({u.username: u.id for u in fresh_users})
    # user_profile 跟着新建的 user 走；已存在的那批不动（可能是别人建的）。
    profiles = [
        UserProfile(
            user_id=user.id,
            nickname=_nickname(user.username),
            intro=_INTRO,
            avatar_id=(i % 8) + 1,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        for i, user in enumerate(fresh_users)
    ]
    for chunk in _batched(profiles):
        session.add_all(chunk)
        await session.flush()

    for row in dataset.feedback:
        row.author_user_id = ids.get(row.author_handle)
        if row.submitted_by_handle:
            row.submitted_by_user_id = ids.get(row.submitted_by_handle)
    for row in dataset.comments:
        row.author_user_id = ids.get(row.author_handle)

    known_supports = await _known_pairs(
        session, FeedbackSupport.feedback_id, FeedbackSupport.author_handle
    )
    supports = [
        row
        for row in _dedupe(dataset.supports, "feedback_id", "author_handle")
        if (row.feedback_id, row.author_handle) not in known_supports
    ]
    known_likes = await _known_pairs(
        session, FeedbackCommentLike.comment_id, FeedbackCommentLike.author_handle
    )
    likes = [
        row
        for row in _dedupe(dataset.likes, "comment_id", "author_handle")
        if (row.comment_id, row.author_handle) not in known_likes
    ]

    written = {"user": len(fresh_users), "user_profile": len(profiles)}
    if progress:
        # user 那两行也要打出来：第二次跑时它是 0（复用），这是幂等生效的样子 ——
        # 只打反馈那几张表的话，「用户到底建没建」在屏幕上就看不出来。
        print(f"  {'user':24} {len(fresh_users):>7}  （已存在 {len(existing)}，复用）")
        print(f"  {'user_profile':24} {len(profiles):>7}")
    # 反馈要先落地：评论/支持/时间线都指着它。
    plan: tuple[tuple[str, Sequence], ...] = (
        ("feedback", dataset.feedback),
        ("feedback_timeline", dataset.timeline),
        ("feedback_supports", supports),
        ("feedback_comments", dataset.comments),
        ("feedback_comment_likes", likes),
    )
    for label, rows in plan:
        for chunk in _batched(rows):
            session.add_all(chunk)
            await session.flush()
        written[label] = len(rows)
        if progress:
            print(f"  {label:24} {len(rows):>7}")
    await session.commit()
    return written


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


async def purge(
    session: AsyncSession, prefix: str, *, progress: bool = True
) -> dict[str, int]:
    """删掉本脚本造的全部行，按依赖顺序。

    判据**只有前缀**。没有「最近创建的」这类判据 —— 那会误伤同一台库上别人的数据，
    而共享的开发库上别人的数据是常态。可重复跑：第二次删 0 行。
    """
    like = f"{prefix}%"
    removed: dict[str, int] = {}

    async def _delete(label: str, statement) -> None:
        result = await session.execute(statement)
        removed[label] = result.rowcount or 0
        if progress:
            print(f"  {label:24} -{removed[label]:>7}")

    feedback_ids = select(Feedback.id).where(Feedback.author_handle.like(like))
    user_ids = select(User.id).where(User.username.like(like))

    # 顺序就是依赖顺序：点赞指着评论，评论指着反馈，user_profile 指着 user。
    await _delete(
        "feedback_comment_likes",
        delete(FeedbackCommentLike).where(FeedbackCommentLike.author_handle.like(like)),
    )
    await _delete(
        "feedback_supports",
        delete(FeedbackSupport).where(FeedbackSupport.author_handle.like(like)),
    )
    # 别人回在本脚本评论下面的那些，靠 `parent_id` 的 CASCADE 一起走。
    await _delete(
        "feedback_comments",
        delete(FeedbackComment).where(FeedbackComment.author_handle.like(like)),
    )
    await _delete(
        "feedback_timeline",
        delete(FeedbackTimeline).where(FeedbackTimeline.feedback_id.in_(feedback_ids)),
    )
    # 带前缀的反馈，连同挂在上面的评论/支持/时间线（CASCADE）一起走。
    await _delete("feedback", delete(Feedback).where(Feedback.author_handle.like(like)))
    await _delete(
        "user_profile", delete(UserProfile).where(UserProfile.user_id.in_(user_ids))
    )
    await _delete("user", delete(User).where(User.username.like(like)))
    await session.commit()
    return removed


async def _purge_preview(session: AsyncSession, prefix: str) -> dict[str, int]:
    """dry-run 的 purge：只数，不删。"""
    like = f"{prefix}%"
    feedback_ids = select(Feedback.id).where(Feedback.author_handle.like(like))
    user_ids = select(User.id).where(User.username.like(like))
    probes: tuple[tuple[str, object, object], ...] = (
        ("feedback", Feedback, Feedback.author_handle.like(like)),
        (
            "feedback_comments",
            FeedbackComment,
            FeedbackComment.author_handle.like(like),
        ),
        (
            "feedback_supports",
            FeedbackSupport,
            FeedbackSupport.author_handle.like(like),
        ),
        (
            "feedback_comment_likes",
            FeedbackCommentLike,
            FeedbackCommentLike.author_handle.like(like),
        ),
        (
            "feedback_timeline",
            FeedbackTimeline,
            FeedbackTimeline.feedback_id.in_(feedback_ids),
        ),
        ("user", User, User.username.like(like)),
        ("user_profile", UserProfile, UserProfile.user_id.in_(user_ids)),
    )
    counts: dict[str, int] = {}
    for label, model, condition in probes:
        total = await session.scalar(
            select(func.count()).select_from(model).where(condition)  # type: ignore[arg-type]
        )
        counts[label] = int(total or 0)
    return counts


# ---------------------------------------------------------------------------
# 落库闸门
# ---------------------------------------------------------------------------

#: 库名里出现这些词之一，才认这是一台可以随便灌的库。判**词**不判完整名字：
#: `cheese_test_sec1`、`cheese_e2e_7`、`fusion_test` 都得过，写死几个整名等于每换一个
#: CI 槽位就回来改一次。
SEEDABLE_DB_WORDS = frozenset({"test", "dev", "e2e", "seed", "scratch"})


def database_name(url: URL | str) -> str:
    """这个 URL 选中的库，取裸名字（sqlite 那种就是文件名）。"""
    return (make_url(url).database or "").rsplit("/", 1)[-1]


def describe_target(url: URL | str) -> str:
    """连接串的人话形式，密码打掉。

    拒绝的时候要的是「我知道刚才打的是哪台」，不是那个密码本身 —— 这句话会进终端
    回滚、进 CI 日志、进粘贴给别人的截图。
    """
    return make_url(url).render_as_string(hide_password=True)


def looks_like_a_throwaway_database(name: str) -> bool:
    """库名像不像一台可以随便灌的库。

    判据只看**库名**，因为 URL 里再没有别的字段能回答这个问题；而且在这个仓库的两台
    机器上，主机名这条线索**正好是反的**：开发机的库在局域网地址（`192.168.16.7:5432`），
    生产那台反而是回环地址（`scripts/ops/README.md`：PG 是 prod 本机 `127.0.0.1:5433`
    上的 docker `cheesex-pg`）。所以「localhost 就是安全的」这条直觉会**把生产放进来、
    把开发拦在外面**，任何一个按主机名放行的写法都是错的。
    """
    return bool(set(re.split(r"[^a-z0-9]+", name.lower())) & SEEDABLE_DB_WORDS)


def require_seedable_target(url: URL | str, *, allow_any: bool) -> None:
    """`--apply` 之前必须先过这一关，否则抛 `SystemExit`。

    为什么需要它：`settings.database_url` 的默认值是
    `postgresql+asyncpg://postgres:postgres@localhost:5432/cheese`，也就是**开发库**
    —— 一个手滑就是往一台有人正在用的库上灌十几万行，而那台库没有一步回退的备份。
    在这之前，唯一挡着这件事的是 docstring 里那句 ⚠️：`--apply` 跑完 `parse_args`
    就直接开写了，没有任何一行代码读过目标是谁。
    """
    name = database_name(url)
    if looks_like_a_throwaway_database(name):
        return
    if allow_any:
        print(
            f"⚠️  目标库 {name!r} 不像是造数库 ——"
            " `--allow-any-database` 是你自己松的闸。"
        )
        print("    这个脚本会往它里面加几万行，默认那 800 条还是追加的。")
        return
    raise SystemExit(
        f"拒绝在 {name!r} 上写库：这个库名不像是测试/开发库。\n"
        f"  目标: {describe_target(url)}\n"
        "  造数库的库名里带 test / dev / e2e / seed / scratch 之一"
        "（CI 槽位库、cheese_test、fusion_test 都算）。\n"
        "  确实要灌这一台就加 --allow-any-database；只想看形状就别加 --apply。"
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把开发库灌到真实规模，好让反馈中心与后台真的被量过。",
    )
    parser.add_argument("--feedback", type=int, default=800, help="造多少条反馈")
    parser.add_argument("--days", type=int, default=180, help="时间铺开多少天")
    parser.add_argument("--authors", type=int, default=60, help="作者池大小")
    parser.add_argument("--prefix", default="volseed", help="handle/username/邮箱前缀")
    parser.add_argument(
        "--seed", type=int, default=20260921, help="随机种子（固定形状，截图才可比）"
    )
    parser.add_argument(
        "--long-thread", type=int, default=3, help="几条反馈各带 80～200 条回复的楼"
    )
    parser.add_argument(
        "--comment-share",
        type=float,
        default=0.85,
        help="有评论的反馈占比（长楼那几条不占这个比例）",
    )
    parser.add_argument("--purge", action="store_true", help="删掉带前缀的行，不造新的")
    parser.add_argument("--apply", action="store_true", help="真的写库（不加就只打印）")
    parser.add_argument(
        "--allow-any-database",
        action="store_true",
        help="松掉落库闸门：往库名不像造数库的库上写（默认直接拒绝）",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    # 判的是 `engine.url`，不是 `settings.database_url` 那个字符串：`app/core/db.py`
    # 会把同步写法的 URL 规范化成 asyncpg 再建引擎，两处可能不是一个值 —— 闸门要问的
    # 是「马上要写的是哪台」，那就得问那台本身。
    print(f"目标库: {describe_target(engine.url)}")
    if args.apply:
        require_seedable_target(engine.url, allow_any=args.allow_any_database)

    spec = Spec(
        feedback=args.feedback,
        days=args.days,
        authors=args.authors,
        prefix=args.prefix,
        seed=args.seed,
        long_thread=args.long_thread,
        comment_share=args.comment_share,
    )

    async with async_session_factory() as session:
        if args.purge:
            if not args.apply:
                print(f"DRY RUN — 只列出打算删什么。prefix={spec.prefix}\n")
                preview = await _purge_preview(session, spec.prefix)
                for label, count in preview.items():
                    print(f"  {label:24} -{count:>7}")
                print("\n加 --apply 才真的删。")
                return 0
            print(f"purge prefix={spec.prefix}")
            await purge(session, spec.prefix)
            return 0

        dataset = build(spec)
        print(f"计划（prefix={spec.prefix}, seed={spec.seed}, days={spec.days}）")
        for label, count in dataset.counts().items():
            print(f"  {label:24} {count:>7}")
        print(f"  作者池 {spec.authors} 人 + {spec.agents} 个 agent")
        if not args.apply:
            print("\nDRY RUN — 一行都没写。加 --apply 才写。")
            return 0
        print("\n写入：")
        await apply_dataset(session, dataset, spec)
    print("\n完成。清理：--purge --apply")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
