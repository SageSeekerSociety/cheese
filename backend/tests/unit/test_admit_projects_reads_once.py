"""一批项目问额度准入，预算、额度包、窗口只各读一次，不按项目逐条问。

``metering.unpaid_sandboxes`` 以前对每台宿主的项目各问一次 ``admit_project``，
一次是三四条查询，项目数一多往返数跟着涨。现在 ``Ledger.admit_many`` 把一批
payer 的预算、额度包、窗口各合成一条查询。

这里钉两件事：

- N 个项目只发出固定条数的查询，且结论与逐条 ``admit`` 逐项相同（包括预算已花完
  的项目、不限量套餐的项目）；
- 把批量换回逐条，同一条计数断言就红：``test_per_payer_loop_trips_the_count`` 跑
  的就是那个逐条版本，证明计数断言咬得住。
"""

import uuid

import pytest

from app.domain.usage.ledger import Ledger
from app.domain.usage.payers import Payer, Terms

TEAM = 7
MONTHLY = Terms(key="pro", credits_per_period=10.0)
UNLIMITED = Terms(key="max", unlimited=True)
SPENT = uuid.uuid4()


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return iter(self._rows)


class _Session:
    """每次 ``execute`` 记一笔；预算那条查询答 ``SPENT`` 已花完，其余答空。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, stmt):
        text = str(stmt)
        self.statements.append(text)
        if "credits_limit" in text:
            return _Rows([(SPENT, 5.0, 5.0)])
        return _Rows([])


def _payers(n: int) -> dict[uuid.UUID, Payer]:
    ids = [SPENT] + [uuid.uuid4() for _ in range(n - 1)]
    return {
        pid: Payer(
            team_id=TEAM,
            terms=UNLIMITED if i == 1 else MONTHLY,
            project_id=pid,
        )
        for i, pid in enumerate(ids)
    }


@pytest.mark.anyio
async def test_a_batch_reads_a_fixed_number_of_times_and_matches_one_by_one():
    small, large = _Session(), _Session()
    await Ledger(small).admit_many(_payers(3))
    payers = _payers(12)
    batch = await Ledger(large).admit_many(payers)
    assert len(large.statements) == len(small.statements), (
        f"12 个项目查了 {len(large.statements)} 次，3 个项目查了 "
        f"{len(small.statements)} 次"
    )
    single = {pid: await Ledger(_Session()).admit(p) for pid, p in payers.items()}
    assert batch == single
    assert batch[SPENT] is not None
    assert [pid for pid, verdict in batch.items() if verdict is None] == [
        pid for pid in payers if pid != SPENT
    ]


@pytest.mark.anyio
async def test_per_payer_loop_trips_the_count():
    """批量被换回逐条时，上面那条计数断言必红：查询数随项目数涨。"""
    payers = _payers(12)
    batch, looped = _Session(), _Session()
    await Ledger(batch).admit_many(payers)
    for payer in payers.values():
        await Ledger(looped).admit(payer)
    assert len(looped.statements) > len(batch.statements)
