"""A session is not kept where its sandbox cannot be reached.

Observed on dev on 2026-10-15: a tool call of a session whose Cloud host had
gone quiet was answered "执行环境仍在准备" for eleven minutes in a row, twice,
because placement kept handing the session back the host it was already on —
``HostPool.place`` returns a session's host without looking at the pool — and
the sweep gives a host up only after ``LOST_AFTER``. The sandbox is disposable:
what the session had pushed is what counts, so a session whose host stopped
answering is placed on one that answers.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from app.domain.agent_session.models import LEFT_HOST_KEY
from app.domain.identity.actor import Actor
from app.domain.machine import services as machine_services
from app.domain.machine.models import AiStatus, MachineStatus
from app.domain.machine.services import HostPool

pytestmark = pytest.mark.anyio


class _Holder:
    """A holder anyone may use, paid for by nobody."""

    def __init__(self, conversation_id):
        self.id = conversation_id
        self.project_id = uuid.uuid4()
        self.generation = str(uuid.uuid4())

    def home(self) -> dict:
        return {
            "project_id": self.project_id,
            "topic_id": self.id,
            "room_resource_id": self.generation,
        }

    async def require_use(self, db, actor) -> None:
        return None

    async def payer(self, db) -> None:
        return None


class _Repo:
    """Just enough of the pool's repository to observe what the service does.

    ``current_home`` reads the home it holds the way the database would: a home
    whose ``session_id`` was cleared by ``leave`` is nobody's.
    """

    def __init__(self, home, hosts, *, load=None, cached=()):
        self.home = home
        self.hosts = list(hosts)
        self.load = dict(load or {})
        self.cached = list(cached)
        self.placed: list[tuple[uuid.UUID, dict]] = []
        self.deleted: list = []
        self.locked = 0

    async def lock_pool(self):
        self.locked += 1

    async def current_home(self, session_id):
        if self.home is not None and self.home.session_id == session_id:
            return self.home
        return None

    async def lock_home(self, home_id):
        return self.home

    async def get(self, host_id):
        return next((host for host in self.hosts if host.id == host_id), None)

    async def live(self):
        return list(self.hosts)

    async def occupancy(self):
        return dict(self.load)

    async def stores(self, project_key):
        return set(self.cached)

    async def put_home(self, host_id, home):
        self.placed.append((host_id, home))
        return SimpleNamespace(host_id=host_id, **home)

    async def delete_home(self, home):
        self.deleted.append(home)


class _NoHomeAtFirstRead(_Repo):
    """The session has no sandbox when placement looks, and is on ``home`` by
    the time it has taken the pool lock — another request of the same session
    placed it in between. Placement decides twice, and both readings find the
    host by different routes.
    """

    def __init__(self, home, hosts, *, load=None):
        super().__init__(None, hosts, load=load)
        self.later = home
        self.reads = 0

    async def current_home(self, session_id):
        self.reads += 1
        if self.reads == 1:
            return None
        return self.later if self.later.session_id == session_id else None

    async def lock_home(self, home_id):
        return self.later


class _Session:
    """The session row, plus the flush/commit calls placement makes."""

    def __init__(self, row):
        self.row = row
        self.commits = 0
        self.flushes = 0

    async def get(self, model, session_id):
        return self.row

    async def refresh(self, obj):
        return obj

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def scalar(self, query):
        # The session's execution request, read to see whether a thaw waits.
        return self.row.execution_request


class _Hub:
    def __init__(self, online):
        self.online = set(online)

    def is_online(self, device_id):
        return device_id in self.online

    def reconnecting(self, device_id):
        return False


def _host(device_id, *, cores=4, memory_mb=8192, disk_gb=100, whole=False, **kw):
    base = dict(
        id=uuid.uuid4(),
        device_id=device_id,
        status=MachineStatus.running,
        ai_status=AiStatus.disabled,
        ai_mode="none",
        whole_machine=whole,
        warm_claim_pending=False,
        machine_id=207,
        released_at=None,
        failed_at=None,
        disk_full_since=None,
        enroll_attempts=1,
        enabled=True,
        cores=cores,
        memory_mb=memory_mb,
        disk_gb=40,
        sandbox_disk_gb=disk_gb,
        created_at=datetime.now(UTC) - timedelta(hours=1),
        enrolled_at=datetime.now(UTC) - timedelta(hours=1),
        idle_since=None,
        draining_since=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Case:
    """One session whose sandbox is on ``host``, and the pool around it."""

    def __init__(
        self, host, session_id, *, others=(), load=None, first_read_empty=False
    ):
        self.home = SimpleNamespace(
            id=uuid.uuid4(),
            host_id=host.id,
            session_id=session_id,
            conversation_id=uuid.uuid4(),
            stopped_at=None,
            active_at=None,
            frozen_at=None,
        )
        self.row = SimpleNamespace(
            conversation_id=self.home.conversation_id,
            work_lease={"status": "ready", "device_id": host.device_id},
            execution_request=None,
        )
        self.repo = (_NoHomeAtFirstRead if first_read_empty else _Repo)(
            self.home, [host, *others], load=load
        )
        self.session = _Session(self.row)
        self.service = HostPool.__new__(HostPool)
        self.service._repo = self.repo  # type: ignore[attr-defined]
        self.service._session = self.session  # type: ignore[attr-defined]
        self.holder = _Holder(self.home.conversation_id)
        self.service._lock_holder = self._lock_holder  # type: ignore[method-assign]

    async def _lock_holder(self, holder_id):
        return self.holder

    def place(self, session_id, hub):
        self.service._hub = hub  # type: ignore[attr-defined]
        return self.service.place(
            session_id, actor=cast(Actor, SimpleNamespace()), resource_id="work"
        )

    def leave_unusable(self, session_id, host_id, hub):
        self.service._hub = hub  # type: ignore[attr-defined]
        return self.service.leave_unusable(session_id, host_id)


@pytest.fixture(autouse=True)
def _no_other_dependencies(monkeypatch):
    """Placement's neighbours, which these cases are not about: whose the
    sandbox is, and the charge for starting an instance."""

    async def _holder_of(session, conversation_id):
        return conversation_id

    async def _admit_start(session, payer):
        return None

    monkeypatch.setattr(machine_services.holders, "holder_of", _holder_of)
    monkeypatch.setattr(machine_services, "admit_start", _admit_start)


async def test_a_session_is_placed_on_a_host_that_answers():
    """The failure this change exists for. The host the session is on is still
    ``_keeps``-true — the pool has not given it up — so placement answered it
    the same host on every call, and the session waited the whole tool call for
    a machine that was not there."""
    session_id = uuid.uuid4()
    quiet = _host("dev-quiet")
    answering = _host("dev-answering")
    case = _Case(quiet, session_id, others=[answering])

    host = await case.place(session_id, _Hub({"dev-answering"}))

    assert host.id == answering.id, "the session was left on a host that is not there"
    assert case.repo.placed and case.repo.placed[0][0] == answering.id
    assert case.home.session_id is None, "the session still holds the old home"
    # And it is told: what it had there is gone, and a new sandbox is what its
    # next tool call runs in.
    assert case.row.execution_request["sandbox_lost"] is True


async def test_a_session_stays_where_it_is_when_no_other_host_answers():
    """Leaving is only an improvement when there is somewhere to go: with no
    host that answers, the session would lose its slot and wait for the same
    machine anyway."""
    session_id = uuid.uuid4()
    quiet = _host("dev-quiet")
    also_quiet = _host("dev-also-quiet")
    case = _Case(quiet, session_id, others=[also_quiet])

    host = await case.place(session_id, _Hub(set()))

    assert host.id == quiet.id
    assert case.home.session_id == session_id, "the session left with nowhere to go"
    assert case.repo.placed == []
    assert case.row.execution_request is None


async def test_a_host_that_still_answers_keeps_the_session():
    """A connector that is up is not a host to move off, whatever the pool
    around it offers."""
    session_id = uuid.uuid4()
    here = _host("dev-here")
    other = _host("dev-other")
    case = _Case(here, session_id, others=[other])

    host = await case.place(session_id, _Hub({"dev-here", "dev-other"}))

    assert host.id == here.id
    assert case.home.session_id == session_id
    assert case.repo.placed == []


async def test_a_whole_vm_is_waited_for_where_it_is():
    """A session's own VM is not a slot to give up: there is no other host to
    put it on, and its machine coming back is the only thing that helps."""
    session_id = uuid.uuid4()
    own = _host("dev-own", whole=True)
    other = _host("dev-other")
    case = _Case(own, session_id, others=[other])

    moved = await case.leave_unusable(session_id, own.id, _Hub({"dev-other"}))

    assert moved is False
    assert case.home.session_id == session_id


async def test_a_host_that_is_already_reconnecting_keeps_the_session():
    """A link that dropped moments ago is waited for, the same line
    ``session_work._reachable`` draws before a call: moving off it would throw
    away a sandbox that is very likely still there, and the call it is about to
    make would have answered anyway."""

    class _Reconnecting(_Hub):
        def reconnecting(self, device_id):
            return device_id == "dev-quiet"

    session_id = uuid.uuid4()
    quiet = _host("dev-quiet")
    other = _host("dev-other")
    case = _Case(quiet, session_id, others=[other])

    host = await case.place(session_id, _Reconnecting({"dev-other"}))

    assert host.id == quiet.id
    assert case.home.session_id == session_id
    assert case.repo.placed == []


@pytest.mark.parametrize(
    "lease",
    [
        # A ready lease naming another machine: whatever this host holds is not
        # the sandbox the session is running in.
        {"status": "ready", "device_id": "dev-somewhere-else"},
        # Not ready: there is no working sandbox of this session anywhere for
        # the move to have taken away.
        {"status": "preparing", "device_id": "dev-quiet"},
    ],
    ids=["lease-on-another-machine", "lease-not-ready"],
)
async def test_only_a_sandbox_that_was_here_is_declared_lost(lease):
    """``LOST_KEY`` is what tells the session that what it had on this host is
    gone, and its next call then says so to the room. A move is still a move —
    but a session whose live sandbox was never on this host is not told it lost
    one."""
    session_id = uuid.uuid4()
    quiet = _host("dev-quiet")
    other = _host("dev-other")
    case = _Case(quiet, session_id, others=[other])
    case.row.work_lease = lease

    moved = await case.leave_unusable(session_id, quiet.id, _Hub({"dev-other"}))

    assert moved is True
    assert case.home.session_id is None
    assert case.row.execution_request is None


async def test_a_host_that_stopped_answering_is_left_the_second_time_too():
    """Placement looks at the session's host twice, the second time under the
    pool lock, and it is the second look that finds one on a host that stopped
    answering: the first found none — another request of the same session placed
    it in between. Only the first look answered the session its host without
    looking at the pool, so only fixing that one would leave this route pinned
    exactly the same way."""
    session_id = uuid.uuid4()
    quiet = _host("dev-quiet")
    answering = _host("dev-answering")
    case = _Case(quiet, session_id, others=[answering], first_read_empty=True)

    host = await case.place(session_id, _Hub({"dev-answering"}))

    assert host.id == answering.id, "the session was left on a host that is not there"
    assert case.home.session_id is None, "the session still holds the old home"
    assert case.row.execution_request["sandbox_lost"] is True


async def test_a_session_home_not_on_that_host_is_not_moved_by_it():
    """``leave_unusable`` names a host; a home that is not on it is somebody
    else's business — the session may have moved already between the two
    reads."""
    session_id = uuid.uuid4()
    quiet = _host("dev-quiet")
    elsewhere = _host("dev-elsewhere")
    case = _Case(quiet, session_id, others=[elsewhere])

    moved = await case.leave_unusable(session_id, elsewhere.id, _Hub({"dev-elsewhere"}))

    assert moved is False
    assert case.home.session_id == session_id


async def test_a_sandbox_asked_for_again_is_not_placed_back_on_the_host_it_left():
    """A room that asks for the sandbox it already has asks for another one
    (``session_work.switch_environment``), and the host it leaves is the one
    placement prefers: it has the fewest free slots left, so the other hosts can
    empty and be released (``rank``). Without avoiding the host it left, that
    preference answers the request with the machine it was asking to get away
    from — the cloud host whose disk is full, which is where FB-101 left sandbox
    switches."""
    session_id = uuid.uuid4()
    left = _host("dev-left")
    other = _host("dev-other")
    case = _Case(left, session_id, others=[other])
    case.repo.home = None  # the session left it; nothing of it is there now
    case.repo.load = {left.id: 1, other.id: 0}
    case.row.execution_request = {LEFT_HOST_KEY: str(left.id)}

    host = await case.place(session_id, _Hub({"dev-left", "dev-other"}))

    assert host.id == other.id, "the session was placed back on the host it left"
    assert case.repo.placed and case.repo.placed[0][0] == other.id
    assert LEFT_HOST_KEY not in case.row.execution_request, (
        "the host it left is still remembered after it has a sandbox"
    )


async def test_a_session_goes_back_to_the_host_it_left_when_no_other_has_room():
    """Avoiding the host it left is a preference, not a refusal: a session with
    no sandbox is worse off than one on the machine it asked to get away from,
    and the pool is not grown for a request that is already answered."""
    session_id = uuid.uuid4()
    left = _host("dev-left")
    full = _host("dev-full")
    case = _Case(left, session_id, others=[full])
    case.repo.home = None
    case.repo.load = {left.id: 1, full.id: 2}  # `full` has none to give
    case.row.execution_request = {LEFT_HOST_KEY: str(left.id)}

    host = await case.place(session_id, _Hub({"dev-left", "dev-full"}))

    assert host.id == left.id, "the session was left with no sandbox at all"
    assert LEFT_HOST_KEY not in case.row.execution_request
