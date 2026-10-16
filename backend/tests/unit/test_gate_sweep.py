"""扫底判死一张张卡的排布 —— 同时在判几张、一张出错时别的卡怎么办。

判死一张卡等的是两样本进程自己算不出来的东西：它那个事务，和叫醒芝士的那一条
`nudge`，其余全是数据库的往返。所以一轮扫底该是同时判几张（`sweep` 的 docstring
写了为什么），而它交回的 `condemned` / `errors` 仍然要和一张一张判时一模一样。
这里钉的就是这两条，加上「一张卡判失败只算它自己」。

替掉的是 `sweep` 的四个外界：查得着什么卡、判死一张卡写成什么样、会话工厂、以及
每个会话上的 `commit` / `rollback`。替身 `condemn` 真的等一会儿再交回 —— 不等就
分不出并发和串行，一张判完才轮到下一张。
"""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.review import gate_sweep
from app.domain.review.models import AcceptStatus

NOW = datetime(2026, 10, 16, tzinfo=UTC)
#: 替身判死一张卡的默认耗时。够长，够让同一轮里别的卡挤进来。
HOW_LONG_S = 0.02


class _Session:
    """`sweep` 在一个会话上只用得着这两种收尾，也就只提供这两种。"""

    def __init__(self, owner: "_Sessions") -> None:
        self._owner = owner

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        self._owner.commits += 1

    async def rollback(self) -> None:
        self._owner.rollbacks += 1


class _Sessions:
    """`SessionFactory` 的替身：开的会话自己就是上下文管理器。"""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def __call__(self) -> _Session:
        return _Session(self)


class _Repo:
    """`AcceptCardRepository` 的替身 —— `sweep` 只用它按 id 查一张卡。"""

    def __init__(self, by_id: dict[uuid.UUID, object]) -> None:
        self.by_id = by_id

    def __call__(self, session: object) -> "_Repo":
        return self

    async def get(self, card_id: uuid.UUID):
        return self.by_id.get(card_id)


class _Condemning:
    """替掉 `condemn`：记下同时在判几张，并真的等一会儿再交回。"""

    def __init__(
        self,
        *,
        how_long: dict[uuid.UUID, float] | None = None,
        failing: frozenset[uuid.UUID] = frozenset(),
    ) -> None:
        self.how_long = how_long or {}
        self.failing = failing
        self.in_flight = 0
        self.peak = 0
        self.finished: list[uuid.UUID] = []

    async def __call__(self, session: object, card: SimpleNamespace) -> None:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.how_long.get(card.id, HOW_LONG_S))
            if card.id in self.failing:
                raise RuntimeError("这张卡判不下去")
            self.finished.append(card.id)
        finally:
            self.in_flight -= 1


def _card(settled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        status=AcceptStatus.accepted if settled else AcceptStatus.pending_gate,
    )


def _world(
    monkeypatch: pytest.MonkeyPatch,
    cards: list[SimpleNamespace],
    *,
    settled: frozenset[uuid.UUID] = frozenset(),
    failing: frozenset[uuid.UUID] = frozenset(),
    how_long: dict[uuid.UUID, float] | None = None,
) -> SimpleNamespace:
    """把一轮 `sweep` 的四个外界换成桩。

    `settled` 里那几张卡是「从查出它到再查它之间，闸门刚好结算完了」：交给
    `get` 的时候状态已经不是 `pending_gate`。
    """
    sessions = _Sessions()
    condemning = _Condemning(how_long=how_long, failing=failing)
    nudged: list[uuid.UUID] = []

    async def find_abandoned(session: object, *, now: datetime | None = None):
        return cards

    async def nudge(topic_id: uuid.UUID, content: str, event: str, meta: dict) -> None:
        nudged.append(topic_id)

    by_id = {
        card.id: (
            SimpleNamespace(id=card.id, status=AcceptStatus.accepted)
            if card.id in settled
            else card
        )
        for card in cards
    }
    monkeypatch.setattr(gate_sweep, "find_abandoned", find_abandoned)
    monkeypatch.setattr(gate_sweep, "condemn", condemning)
    monkeypatch.setattr(gate_sweep, "AcceptCardRepository", _Repo(by_id))

    async def run() -> dict:
        return await gate_sweep.sweep(sessions, nudge=nudge, now=NOW)

    return SimpleNamespace(
        run=run, sessions=sessions, condemning=condemning, nudged=nudged
    )


async def test_cards_are_judged_at_once_and_no_more_than_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一轮该同时判几张：多于一张，但一次不超过 `AT_ONCE`。"""
    cards = [_card() for _ in range(gate_sweep.AT_ONCE * 2)]

    world = _world(monkeypatch, cards)
    result = await world.run()

    assert world.condemning.peak > 1
    assert world.condemning.peak == min(gate_sweep.AT_ONCE, len(cards))
    assert result["condemned"] == [card.id for card in cards]


async def test_one_cards_failure_leaves_the_others_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一张卡判失败只算它自己：别的卡照判、照提交，它自己回滚。

    这是「一张卡出错不能把另一张卡已经判死的结果回滚掉」那一条在并发之后仍然
    成立的样子 —— 同时判不等于共用一次成败。
    """
    cards = [_card() for _ in range(4)]
    doomed = cards[1]

    world = _world(monkeypatch, cards, failing=frozenset({doomed.id}))
    result = await world.run()

    assert result["condemned"] == [c.id for c in (cards[0], cards[2], cards[3])]
    assert result["errors"] == [f"{doomed.id}: 这张卡判不下去"]
    assert [c.id for c in (cards[0], cards[2], cards[3])] == world.condemning.finished
    # 判死一张卡一个事务：四张卡四个事务，失败的只回滚了自己那一个。
    assert world.sessions.commits == 3
    assert world.sessions.rollbacks == 1
    # 没判死的卡不叫人：那一轮是给芝士去重递的。
    assert world.nudged == [c.topic_id for c in (cards[0], cards[2], cards[3])]


async def test_the_report_keeps_the_order_the_cards_came_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交回的列表按查出卡片的顺序，不按判完的先后。

    所以这里让先查出的卡判得最慢：完成顺序和查出的顺序正好反过来。
    """
    cards = [_card() for _ in range(4)]
    settled = frozenset({cards[2].id})
    how_long = {
        card.id: HOW_LONG_S * (len(cards) - index) for index, card in enumerate(cards)
    }

    world = _world(monkeypatch, cards, settled=settled, how_long=how_long)
    result = await world.run()

    assert world.condemning.finished == [
        cards[3].id,
        cards[1].id,
        cards[0].id,
    ], "先查出的卡该后判完"
    assert result["condemned"] == [cards[0].id, cards[1].id, cards[3].id]
    assert result["errors"] == []
    # 已经结算掉的卡既不判死也不记错，当然也不叫人。
    assert cards[2].topic_id not in world.nudged
