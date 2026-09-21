"""写进记忆之前问一句：这件事 repo 里是不是已经写着了。

结论 61：「memory 只记 repo 里查不到的」。判据不能是提示词里的一句叮嘱——叮嘱只
在模型愿意照做的时候成立，而写入端每天要挡的正是它没照做的那些次。所以在存之前
对着这条活的检出目录真跑一次内容检索，命中就拒绝，并说出命中在哪个文件：拒绝理由
里没有那个文件路径，agent 除了把同一句话换个说法再写一遍之外无事可做。
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.keywords import match_content, query_terms

logger = logging.getLogger("cheesex.memory")

#: 一行要覆盖这条事实多少关键词才算「repo 里已经写着了」。
#:
#: 比 `cheese recall` 的 `is_relevant` 严，而且只认覆盖度、不认「命中了一个整词」：
#: recall 宁可多给几条让 agent 自己挑，这里命中一次就是拒掉一次写入，而记忆是显式
#: 写进去、不可再生的（结论 61）——拒错了那条事实就没有第二次机会。只共用一个词
#: （`pytest`、`alembic`）远不足以说明同一件事已经写在代码里了。
ENOUGH_OF_THE_FACT = 0.6

#: 拿权重最高的几个词去检索，机器那边把它们**与**起来。
#:
#: 送 12 个词过去、每个词各算一次命中，等于问「这一行里有没有出现过其中任何一个
#: 词」——`npm` 这种词在真前端仓里几百个文件都有，结果窗口在路径序靠前的目录里就
#: 用完了，真正写着这件事的那一行根本排不进来，判据在它唯一该生效的场景里静默失
#: 效。与起来问的是「这几个最重的词同时出现在一行里」，那才是下面 `ENOUGH_OF_THE
#: _FACT` 要量的东西。
#:
#: 三个：覆盖度要到 0.6，最重的那几个词几乎必然在那一行里；多与一个词只会多漏，
#: 而漏了就是多记一条重复的，那一侧是便宜的（见 `room_checkout_search`）。
_TERMS_OF_THE_FACT = 3

#: 等那台机器多久。`cheese remember` 是一次交互调用，不是一条活。
_SEARCH_TIMEOUT_S = 15


@dataclass(frozen=True)
class RepoHit:
    """检出目录里那一行：路径、行号、原文。路径是拒绝理由的全部价值所在。"""

    path: str
    line: int
    text: str


#: 对着检出目录按关键词找行。给一组关键词，还回 `{"path", "line", "text"}` 的列表。
CheckoutSearch = Callable[[list[str]], Awaitable[list[dict]]]


async def already_in_repo(text: str, search: CheckoutSearch) -> RepoHit | None:
    """这条事实是不是 repo 里已经写着的；是就还回命中的那一行。

    关键词的切法与 `cheese recall` 共用 `memory.keywords`：同一套切分同时决定「以
    后怎么查得到这条记忆」和「它跟 repo 里的一行算不算同一件事」，两边各写一套的
    那一天，就会出现存得进去、却查不出来的事实。
    """
    terms = query_terms(text)
    if not terms:
        return None
    hits = await search([term for term, _ in terms][:_TERMS_OF_THE_FACT])
    best: RepoHit | None = None
    best_coverage = 0.0
    # 全看一遍取覆盖度最高的那一行，不是遇到第一条过阈值的就停：拒绝理由只带一个
    # 路径，而带哪一个决定了写入方接下来读的是不是真正写着这件事的那份文件。
    for hit in hits:
        line = str(hit.get("text") or "")
        coverage, _ = match_content(terms, line)
        if coverage >= ENOUGH_OF_THE_FACT and coverage > best_coverage:
            best_coverage = coverage
            best = RepoHit(
                path=str(hit.get("path") or ""),
                line=int(hit.get("line") or 0),
                text=line.strip()[:200],
            )
    return best


#: 「这里本来就没有检出可查」的那几个理由。私聊没有租机器（结论 19），房间的机器
#: 还没上来也一样——这两种每天都会发生，按 warning 记就等于把真正的告警淹掉。
#: 其余每一种都是查不成：那台机器上的 git 用不了、执行器不认得这个方法、调用炸了。
_NOTHING_TO_SEARCH = frozenset({"no-checkout", "no-device"})


def room_checkout_search(session: AsyncSession, room_id: uuid.UUID) -> CheckoutSearch:
    """在这间房那台机器的检出目录里检索。

    检出在手上，不在平台上（结论 22、60），所以这是一次执行器调用。够不着就还回
    空：没有机器、机器离线、或者那台机器上的执行器还不认得这个方法，都只说明**这
    一次没查成**，而不是「repo 里没有」。查不成就存下来——挡住一条本该记下的事实
    是真丢数据，多记一条重复的不是（结论 61，记忆不可再生）。

    **但是要说出来。**这一路上「repo 里确实没写」和「这次没查成」在返回值里长得
    一模一样，而 P35 的验收正是靠这条判据——它在某台机器上从此一条都不拦，外面一
    个信号都没有的话，没有人会发现。所以每一次没查成都留一行日志，带上是哪一种。
    """

    def nothing_came_back(reason: str) -> list[dict]:
        log = logger.info if reason in _NOTHING_TO_SEARCH else logger.warning
        log("repo_search did not run for room=%s: %s", room_id, reason)
        return []

    async def search(terms: list[str]) -> list[dict]:
        from app.domain.agent import execution
        from app.domain.agent_session.services import AgentSessionService

        try:
            target = next(
                (
                    place.lease
                    for place in await AgentSessionService(session).places_in_room(
                        room_id
                    )
                    if (place.lease or {}).get("kind") == "device"
                ),
                None,
            )
            if target is None:
                return nothing_came_back("no-device")
            # 记一条记忆等不起一台慢机器：查不成就存下来，见 docstring。
            async with asyncio.timeout(_SEARCH_TIMEOUT_S):
                result = await execution.call(target, "repo_search", {"terms": terms})
        except Exception as exc:  # noqa: BLE001 — 查不成是一种答案，不是失败的写入
            logger.warning(
                "repo_search did not run for room=%s: %s", room_id, exc, exc_info=True
            )
            return []
        if not result.get("searched"):
            return nothing_came_back(str(result.get("reason") or "unknown"))
        hits = result.get("hits")
        return hits if isinstance(hits, list) else []

    return search
