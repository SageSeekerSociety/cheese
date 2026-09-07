"""合并态判定 —— issue #718 的「算 mergeStateStatus 这一个函数」。

纯函数、无 IO：给定 GitHub 侧信号（REST `mergeable_state` / `mergeable` /
check runs / 改动文件清单 / 与基线的 ancestry）和项目的分支保护参数
（required_checks、strict），算出卡片显示与采纳依据的合并态。点击时和轮询时
都调它，判定只存在这一份。

规则（issue #718 的模型）：

- **GitHub 能判定的听 GitHub**（``github_enforces=True``，即项目绑了 GitHub 且
  GitHub 自己开了分支保护）：状态原样透传，平台一个字不重算。reasons 里仍会
  从 check runs 补出「哪个检查红了 / CI 还在跑」——那是给「谁的活」标签用的
  注解，不改变 state。
- **判定不了的平台补位**（``github_enforces=False``）：必跑检查有红 → blocked
  （哪个红写进 reason）；必跑检查缺席 → blocked（等 CI；**缺席是 pending，
  不是通过**，#468/#465）；strict 且落后基线 → behind；GitHub 说 dirty（或
  ``mergeable is False``）→ dirty；unstable 但红的不在必跑名单（或名单为空）
  → unstable，可采纳。
- **draft**：无论哪种模式都是 blocked（GitHub 对 draft 的合并接口直接拒绝），
  reason kind 为 ``draft``。REST 的 ``mergeable_state == "draft"`` 和调用方
  显式传入 ``draft=True`` 等价。
- **unknown**：两个来源，都落到 state ``unknown``——GitHub 说 ``"unknown"``
  （push 后异步计算还没算完），或根本没有信号（``mergeable_state=None`` 且
  没有别的规则命中）。unknown 不可采纳也不报警：下一轮读到真值自然收敛。
- **未绑 GitHub 的项目**走 :func:`local_merge_state`：唯一的信号是「与基线是否
  冲突」，True → dirty，False → clean，None → unknown。

必跑名单的路径域语义与旧轮询器一致（#470）：带路径的条目只对碰了那些路径的
改动生效；``changed_paths=None``（拿不到 diff）时**保守处理**——照样算必需，
拿一次 API 失败换掉整道阀是不行的。glob 是 GitHub Actions `paths:` 那套的
子集：``**`` 跨目录、``*`` 不跨目录、``?`` 单字符，整串匹配。

ancestry 的词表沿用 compare API 的 ``status``（ahead/behind/diverged/
identical）；``behind`` 和 ``diverged`` 都算落后（两个各自绿在旧基上的 PR
相加可以是红的，#468），``None``（读不到）不拦——ancestry 读不到不该冻结
整条采纳路。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

#: 卡上的状态词 —— GraphQL ``mergeStateStatus`` 的小写。``draft`` 不在其中：
#: 它不是「可不可以合」的一档而是「这个 PR 还没做完」，判定归入 blocked，
#: reason kind 说明是 draft。
MergeState = Literal["clean", "unstable", "blocked", "behind", "dirty", "unknown"]

#: reason 的机器可读分类。issue「谁的活」表格靠它分流：
#: ``ci_running`` → 等 CI；``required_check_failed`` / ``check_failed`` →
#: 芝士处理（带检查名）；``required_check_missing`` → 等 CI（超宽限期转人，
#: 宽限期是调用方的事，这里是纯函数）；``behind_base`` → 平台更新分支；
#: ``conflict`` → 芝士解冲突；``draft`` → 还没递；``github_verdict`` →
#: 透传模式下 GitHub 的原话；``no_obstacle`` → clean，没有任何东西拦着；
#: ``no_signal`` → 什么都还不知道。
ReasonKind = Literal[
    "github_verdict",
    "conflict",
    "draft",
    "required_check_failed",
    "required_check_missing",
    "check_failed",
    "ci_running",
    "behind_base",
    "no_obstacle",
    "no_signal",
]

#: check run conclusion 里算「红」的那些词（GitHub 的原词）。``skipped`` 不在
#: 此列也不算过：一个**必跑**的检查报 skipped 等于它对这次改动没跑过，按
#: 缺席处理（#465 的教训：看见的都绿不等于测试跑过）。
_RED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)
_PASS_CONCLUSIONS = frozenset({"success", "neutral"})


@dataclass(frozen=True)
class RequiredCheck:
    """必跑名单的一条：检查名 + 「什么样的改动才要求它出现」。

    ``paths`` 为空 = 无条件要求。非空 = 只有当改动命中其中某条 glob 时才要求
    ——workflow 自己带路径过滤，纯前端 PR 上 `test` 永远不会出现，把缺席一律
    读作「还在等」就是无限等（#470）。"""

    name: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckRun:
    """一个 check run 判定需要的三个字段。``conclusion`` 在跑完前是 None。"""

    name: str
    status: str  # "queued" | "in_progress" | "completed"
    conclusion: str | None = None


@dataclass(frozen=True)
class MergeReason:
    """结论的一条依据。``checks`` 是涉及的检查名（排好序，卡面直接可用）。"""

    kind: ReasonKind
    checks: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class MergeVerdict:
    """判定结果：state 是卡上的词，reasons 是它的依据（可能多条，比如
    既有红检查又有缺席的）。reasons 至少一条。"""

    state: MergeState
    reasons: tuple[MergeReason, ...]


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """GitHub Actions 路径过滤那套 glob 编译成正则。

    只实现 workflow `paths:` 里真正会写的三个通配：``**``（跨目录）、``*``
    （不跨目录）、``?``（单字符）。`fnmatch` 不能用——它的 `*` 会跨 `/`，
    那样 `frontend/*.ts` 会把 `backend/a/b.ts` 也算命中，等于把阀关掉。"""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _applies(
    check: RequiredCheck, changed_paths: Sequence[str] | None
) -> tuple[bool, bool]:
    """这条名单项对这次改动是否必需 → (必需, 是否走了保守回退)。

    保守回退 = 带路径条件但 ``changed_paths is None``（拿不到 diff）：不知道
    这次碰没碰那些路径，照样算必需——等下去不会误合，放行等于用一次 API 失败
    换掉整道阀。回退要能被外面看见（第二个返回值），卡面得如实说。"""
    if not check.paths:
        return True, False
    if changed_paths is None:
        return True, True
    return (
        any(_glob_regex(p).match(path) for path in changed_paths for p in check.paths),
        False,
    )


_CheckOutcome = Literal["pass", "running", "red", "skipped", "absent"]


def _outcome(name: str, check_runs: Sequence[CheckRun]) -> _CheckOutcome:
    """名字为 ``name`` 的检查在这批 runs 里的净结论。

    同名可能出现多次（re-run 后新旧都在列表里）：任何一次过了就算过——
    GitHub 自己也是拿最新一次算的，而重跑绿掉一个红检查正是重跑的意义；
    没过的里面只要还有在跑的就是在跑；跑完的全红才是红；全 skipped 视同
    没跑过（见 ``_RED_CONCLUSIONS`` 的注释）。"""
    runs = [r for r in check_runs if r.name == name]
    if not runs:
        return "absent"
    conclusions = {r.conclusion for r in runs if r.status == "completed"}
    if conclusions & _PASS_CONCLUSIONS:
        return "pass"
    if any(r.status != "completed" for r in runs):
        return "running"
    if conclusions & _RED_CONCLUSIONS:
        return "red"
    return "skipped"


def _check_annotations(check_runs: Sequence[CheckRun]) -> tuple[MergeReason, ...]:
    """从全部 check runs 补出「哪个红了 / 哪些还在跑」的注解 reasons。

    透传模式和 unstable 放行时用：state 不由它决定，「谁的活」标签由它决定
    （issue #718 的表：UNSTABLE 里「CI 在跑」和「检查红了」发给不同的人）。"""
    names = sorted({r.name for r in check_runs})
    red = tuple(n for n in names if _outcome(n, check_runs) == "red")
    running = tuple(n for n in names if _outcome(n, check_runs) == "running")
    out: list[MergeReason] = []
    if red:
        out.append(
            MergeReason(
                kind="check_failed",
                checks=red,
                detail="检查红了：" + ", ".join(red),
            )
        )
    if running:
        out.append(
            MergeReason(
                kind="ci_running",
                checks=running,
                detail="CI 还在跑：" + ", ".join(running),
            )
        )
    return tuple(out)


def _normalize(github_mergeable_state: str | None) -> str:
    return (github_mergeable_state or "").strip().lower()


def _passthrough(
    state_word: str,
    check_runs: Sequence[CheckRun],
) -> MergeVerdict:
    """``github_enforces=True``：GitHub 的裁决原样透传，平台一个字不重算。

    ``draft`` 归入 blocked（reason 说明），``has_hooks``（带 pre-receive hook
    的 clean）归入 clean，认不出的词和 ``unknown`` 都归 unknown。"""
    verdict = MergeReason(
        kind="github_verdict", detail=f"GitHub 的裁决：{state_word or 'unknown'}"
    )
    annotations = _check_annotations(check_runs)
    match state_word:
        case "clean" | "has_hooks":
            return MergeVerdict(state="clean", reasons=(verdict,))
        case "unstable" | "blocked" as word:
            state: MergeState = "unstable" if word == "unstable" else "blocked"
            return MergeVerdict(state=state, reasons=(verdict, *annotations))
        case "behind":
            return MergeVerdict(
                state="behind",
                reasons=(verdict, MergeReason(kind="behind_base", detail="落后基线")),
            )
        case "dirty":
            return MergeVerdict(
                state="dirty",
                reasons=(verdict, MergeReason(kind="conflict", detail="与基线冲突")),
            )
        case "draft":
            return MergeVerdict(
                state="blocked",
                reasons=(
                    verdict,
                    MergeReason(kind="draft", detail="还是 draft，没递交评审"),
                ),
            )
        case _:
            return MergeVerdict(state="unknown", reasons=(verdict,))


def compute_merge_state(
    *,
    github_mergeable_state: str | None,
    github_mergeable: bool | None,
    check_runs: Sequence[CheckRun] = (),
    changed_paths: Sequence[str] | None = None,
    required_checks: Sequence[RequiredCheck] = (),
    strict: bool = False,
    base_ancestry: str | None = None,
    github_enforces: bool = False,
    draft: bool = False,
) -> MergeVerdict:
    """绑了 GitHub 的项目的合并态。规则见模块 docstring。

    参数都是原始信号，刻意不 import 任何领域对象：

    - ``github_mergeable_state``：REST PR 响应的 ``mergeable_state``
      （`PullRequestStatus.mergeable_state`），None = payload 里没有。
    - ``github_mergeable``：三值的 ``mergeable``，只有 False 是冲突。
    - ``check_runs``：这个 head 上的 check runs。
    - ``changed_paths``：PR 实际改动的文件（compare API 的 files），None =
      拿不到（截断/失败），路径域名单按保守处理。
    - ``required_checks`` / ``strict``：项目的分支保护参数。
    - ``base_ancestry``：compare API 的 ``status``（ahead/behind/diverged/
      identical），None = 读不到，不拦。
    - ``github_enforces``：GitHub 自己开了分支保护 → 原样透传它的裁决。
    - ``draft``：PR 是 draft（REST 也会在 ``mergeable_state`` 里给 "draft"，
      两个入口等价）。
    """
    state_word = _normalize(github_mergeable_state)

    if github_enforces:
        if draft and state_word not in ("", "draft"):
            # 信号打架时 draft 优先：draft 的合并接口必拒，别的词都是旧的。
            return _passthrough("draft", check_runs)
        return _passthrough(state_word, check_runs)

    # ---- 平台补位 ----------------------------------------------------------

    if draft or state_word == "draft":
        return MergeVerdict(
            state="blocked",
            reasons=(MergeReason(kind="draft", detail="还是 draft，没递交评审"),),
        )

    # 冲突最优先：冲突的 PR 上 CI 说什么都不重要，先解掉。只有 mergeable 是
    # **实打实的 False** 才算（None = GitHub 还没算完，不是冲突）。
    if state_word == "dirty" or github_mergeable is False:
        return MergeVerdict(
            state="dirty",
            reasons=(MergeReason(kind="conflict", detail="与基线冲突"),),
        )

    # 必跑名单（#468/#470 语义）：红 → blocked；缺席/skipped → blocked（等
    # CI，缺席是 pending 不是通过）；还在跑 → 后面归入「等 CI」的 unstable。
    red: list[str] = []
    missing: list[str] = []
    running: list[str] = []
    fallback = False
    for rc in required_checks:
        needed, fell_back = _applies(rc, changed_paths)
        if not needed:
            continue
        fallback = fallback or fell_back
        match _outcome(rc.name, check_runs):
            case "red":
                red.append(rc.name)
            case "absent" | "skipped":
                missing.append(rc.name)
            case "running":
                running.append(rc.name)
            case "pass":
                pass
    fallback_note = (
        "（拿不到这次改动的文件清单，带路径条件的必跑检查按必需处理）"
        if fallback
        else ""
    )
    if red or missing:
        reasons: list[MergeReason] = []
        if red:
            reasons.append(
                MergeReason(
                    kind="required_check_failed",
                    checks=tuple(sorted(red)),
                    detail="必跑检查红了：" + ", ".join(sorted(red)),
                )
            )
        if missing:
            reasons.append(
                MergeReason(
                    kind="required_check_missing",
                    checks=tuple(sorted(missing)),
                    detail="必跑检查还没报到："
                    + ", ".join(sorted(missing))
                    + fallback_note,
                )
            )
        return MergeVerdict(state="blocked", reasons=tuple(reasons))

    # strict up-to-date（#468 阀二）：绿必须绿在当前基线上。behind 和
    # diverged 都算落后；None（读不到）不拦。
    if strict and base_ancestry in ("behind", "diverged"):
        return MergeVerdict(
            state="behind",
            reasons=(
                MergeReason(kind="behind_base", detail=f"落后基线（{base_ancestry}）"),
            ),
        )

    # 必跑检查在跑：等 CI。issue 的表把它归在 UNSTABLE（GitHub 也是），靠
    # reason 和「检查红了」分开。
    if running:
        return MergeVerdict(
            state="unstable",
            reasons=(
                MergeReason(
                    kind="ci_running",
                    checks=tuple(sorted(running)),
                    detail="CI 还在跑：" + ", ".join(sorted(running)),
                ),
            ),
        )

    # 到这里平台的规则全部放行。剩下听 GitHub 的词。
    match state_word:
        case "clean" | "has_hooks":
            return MergeVerdict(
                state="clean",
                reasons=(MergeReason(kind="no_obstacle", detail="可以合并"),),
            )
        case "unstable":
            # 红的（若有）不在必跑名单里，或名单为空 → 可采纳；注解如实说
            # 哪个红、哪个在跑，给「谁的活」标签用。
            annotations = _check_annotations(check_runs)
            return MergeVerdict(
                state="unstable",
                reasons=annotations
                or (
                    MergeReason(
                        kind="github_verdict",
                        detail="GitHub 的裁决：unstable（有检查没过，但不在必跑名单）",
                    ),
                ),
            )
        case "blocked" | "behind" as word:
            # github_enforces=False 却给出这两个词 = GitHub 那边其实有我们
            # 不知道的保护在执行。听它的，不重算。
            state = "blocked" if word == "blocked" else "behind"
            return MergeVerdict(
                state=state,
                reasons=(
                    MergeReason(kind="github_verdict", detail=f"GitHub 的裁决：{word}"),
                ),
            )
        case _:
            # "unknown"（GitHub 还没算完）或 None（payload 没带）。不可采纳
            # 也不报警，下一轮读到真值自然收敛。
            return MergeVerdict(
                state="unknown",
                reasons=(
                    MergeReason(
                        kind="no_signal",
                        detail="GitHub 还没算出这个 PR 的合并态",
                    ),
                ),
            )


#: 「谁的活」（issue #718 的表格）：非 CLEAN 的卡上，状态词旁边标出谁在处理。
#: ``ci`` = 等 CI 跑完，不用叫任何人；``agent`` = 芝士处理中（检查红了/冲突/
#: 还是 draft）；``platform`` = 平台自己会动（update-branch、下一轮重读）；
#: ``human`` = 等人（可采纳，或机器给不出下一步）。
Who = Literal["ci", "agent", "platform", "human"]


def whose_move(verdict: MergeVerdict) -> Who:
    """这个合并态下，下一步在谁手上 —— issue #718「卡上的小圈」那张表。

    纯映射，只读 verdict：

    - clean → human（绿勾，采纳亮，通知验收人）；
    - dirty → agent（芝士把 main 合进来解冲突）；
    - behind → platform（平台 update-branch；撞冲突下一轮变 dirty 才转 agent）；
    - unstable / blocked → 按 reasons 分：有红检查 → agent；只是必跑检查
      没报到或 CI 在跑 → ci；draft → agent（活还没做完）；只剩 GitHub 的
      裁决而看不出细节 → human（机器不猜）；
    - unknown → platform（GitHub 还没算完，下一轮自然收敛，谁都不用动）。

    「必跑检查没报到超过宽限期转人」是调用方的事——宽限期要时钟，这里是纯函数。
    """
    match verdict.state:
        case "clean":
            return "human"
        case "dirty":
            return "agent"
        case "behind":
            return "platform"
        case "unknown":
            return "platform"
    kinds = {r.kind for r in verdict.reasons}
    if kinds & {"required_check_failed", "check_failed"}:
        return "agent"
    if kinds & {"required_check_missing", "ci_running"}:
        return "ci"
    if "draft" in kinds:
        return "agent"
    return "human"


def local_merge_state(*, conflicts_with_trunk: bool | None) -> MergeVerdict:
    """未绑 GitHub 的项目（平台即 forge，#363）的合并态。

    唯一的信号是「与基线是否冲突」：True → dirty，False → clean，None
    （还没试算过）→ unknown。名单/strict 对这类项目没有取数来源，等它们有了
    信号源再走 :func:`compute_merge_state`。"""
    match conflicts_with_trunk:
        case True:
            return MergeVerdict(
                state="dirty",
                reasons=(MergeReason(kind="conflict", detail="与基线冲突"),),
            )
        case False:
            return MergeVerdict(
                state="clean",
                reasons=(MergeReason(kind="no_obstacle", detail="可以合并"),),
            )
        case _:
            return MergeVerdict(
                state="unknown",
                reasons=(
                    MergeReason(kind="no_signal", detail="还没算过与基线是否冲突"),
                ),
            )
