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

#: 一次检索最多带几个关键词过去。`git grep` 每个 `-e` 都是一遍扫描，而权重最高的
#: 那几个已经决定了覆盖度。
_TERMS_PER_SEARCH = 12

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
    hits = await search([term for term, _ in terms][:_TERMS_PER_SEARCH])
    for hit in hits:
        line = str(hit.get("text") or "")
        coverage, _ = match_content(terms, line)
        if coverage >= ENOUGH_OF_THE_FACT:
            return RepoHit(
                path=str(hit.get("path") or ""),
                line=int(hit.get("line") or 0),
                text=line.strip()[:200],
            )
    return None


def room_checkout_search(session: AsyncSession, room_id: uuid.UUID) -> CheckoutSearch:
    """在这间房那台机器的检出目录里检索。

    检出在手上，不在平台上（结论 22、60），所以这是一次执行器调用。够不着就还回
    空：没有机器、机器离线、或者那台机器上的执行器还不认得这个方法，都只说明**这
    一次没查成**，而不是「repo 里没有」。查不成就存下来——挡住一条本该记下的事实
    是真丢数据，多记一条重复的不是（结论 61，记忆不可再生）。
    """

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
                return []
            # 记一条记忆等不起一台慢机器：查不成就存下来，见 docstring。
            async with asyncio.timeout(_SEARCH_TIMEOUT_S):
                result = await execution.call(target, "repo_search", {"terms": terms})
        except Exception:  # noqa: BLE001 — 查不成是一种答案，不是一次失败的写入
            logger.info("repo_search unavailable for room=%s", room_id, exc_info=True)
            return []
        hits = result.get("hits")
        return hits if isinstance(hits, list) else []

    return search
