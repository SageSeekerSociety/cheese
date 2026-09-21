"""A place whose machine is a ledger instead of somebody's hardware.

The seam is `agent/place.py`: a place says what it can do (`capabilities`),
hands out a lease with a term, takes it back with three receipts, and — where
the platform opened the machine itself — puts it to sleep and wakes it inside a
reconnect window.

Everything on the other side of that seam is a machine we do not own: a laptop
somebody enrolled, a VM MicroCloud opened. So the only way to test "does a
return take three receipts, and does a sleep take none" is to hold a place whose
machine we can stop and start by writing a line in a list.

`LedgerPlace` is that place. It records every call in order, and the workspace
it holds is a dict a test can write into — which is what makes "醒来是同一台
机器、工作区原样" an assertion rather than a promise. It is a double, not a mock
of our own code: it answers the real `Place` protocol, so an operation added to
the seam breaks it here rather than leaving a test that passes against a shape
nothing has any more.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.agent import place
from app.domain.device.supply import Supply


class LedgerPlace:
    """一双手，它的机器是一张账本。

    ``supply`` 决定它给不给得出第三态——和真实实现一样，从供给推，不是自己报。
    ``receipts`` 是这台机器现在能交出来的三张，测试直接写它。
    """

    reconnect_window = timedelta(minutes=30)

    def __init__(
        self,
        *,
        supply: Supply = Supply.cloud,
        hands_here: bool = True,
        receipts: place.Receipts | None = None,
        now: datetime | None = None,
    ) -> None:
        self.supply = supply
        self.hands_here = hands_here
        self.receipts = receipts or place.Receipts(
            transcript_stored=True, memory_tidied=True, work_published=True
        )
        self.now = now or datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        #: 机器上的那份工作区。休眠不动它，归还才丢。
        self.workspace: dict[str, str] = {}
        #: 每一次调用，按顺序。
        self.calls: list[str] = []
        self.machine = "ledger-machine"

    def capabilities(self) -> frozenset[str]:
        return place.capabilities_of(self.supply, hands_here=self.hands_here)

    async def acquire(self, term: timedelta | None) -> place.Lease:
        self.calls.append("acquire")
        return place.Lease(
            machine=self.machine,
            resource_id="room-1",
            state=place.LeaseState.in_use,
            expires_at=self.now + term if term is not None else None,
        )

    async def release(self, lease: place.Lease) -> place.Receipts:
        ready, why = place.receipts_ready(place.LeaseState.returned, self.receipts)
        if not ready:
            self.calls.append("release-refused")
            raise RuntimeError(why)
        self.calls.append("release")
        self.workspace.clear()
        return self.receipts

    async def sleep(self, lease: place.Lease) -> place.Lease:
        if place.CAN_SLEEP not in self.capabilities():
            raise RuntimeError("这台机器不是平台开的，平台停不了它")
        # 休眠也过同一个闸门——问的是同一句话，答案才是这条路的性质：进 ``asleep``
        # 这一档一张收据都不要（结论 39）。不问的话，「休眠不取收据」就只是这个替
        # 身自己的实现，闸门哪天改成对休眠也要收据，这里照样绿。
        ready, why = place.receipts_ready(place.LeaseState.asleep, self.receipts)
        if not ready:
            self.calls.append("sleep-refused")
            raise RuntimeError(why)
        self.calls.append("sleep")
        return place.Lease(
            machine=lease.machine,
            resource_id=lease.resource_id,
            state=place.LeaseState.asleep,
            expires_at=lease.expires_at,
            asleep_until=self.now + self.reconnect_window,
        )

    async def wake(self, lease: place.Lease) -> place.Lease:
        if not lease.wakeable(self.now):
            raise RuntimeError("reconnect window 过了，这份租约只能走归还")
        self.calls.append("wake")
        return place.Lease(
            machine=lease.machine,
            resource_id=lease.resource_id,
            state=place.LeaseState.in_use,
            expires_at=lease.expires_at,
        )
