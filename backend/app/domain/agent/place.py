"""A place: what of ours is on a machine we borrow, and until when.

Where the platform's own files sit
----------------------------------

A room's machine belongs to someone else. Everything the platform installs on
it — the executor and its helpers, the CLI, the environment runner, the launch
scripts, the session homes, the shared package store — goes under one root, so
that removing the platform from a machine is deleting one directory rather than
recalling every path some launcher ever wrote. That root is `footprint_root()`,
directly under the machine owner's own `$HOME`, and it is the whole of what
`cheese uninstall` removes.

Inside a session home there is a second name to read. `.claude` is the root
earlier launchers installed into, and a room does not move: it keeps its
executor, its markers and its helpers where the launcher that prepared it put
them until something prepares it again. Reading only the current root answers
"this room never had an executor" for a room that has one running. So the
current root is what we write, and `session_platform_dirs()` is what we read —
in this order, most recent first.

That pair exists INSIDE A SESSION HOME and nowhere else. On the machine's own
`$HOME` the platform has only ever written `footprint_root()`; the `~/.claude`
beside it is the machine owner's — their Claude Code credentials, their
settings, every transcript they have — and nothing here writes it, moves it or
removes it. An uninstall walks the footprint root and stops.

`write()` is the other half of the same rule, and the reason it lives here:
what the platform writes on a borrowed machine goes under this root, and the
hosted checkout is not under it. One function means one assertion rather than a
path-prefix argument spread over every caller (结论 49，不变量 I21b).

Six programs cannot ask this module and carry the names themselves: the
environment runner, the cleanup script, the executor bootstrap, the session
transfer and the sandbox CLI (`backend/sandbox/cheese`) all run where nothing of
ours is importable — two are piped in on stdin and have no `__file__` to look
at, one is written out beside the room's own files and run as a script, one is
exec'd out of a string, one is shipped into the agent's container as a
standalone program — and the connector is Go. Their copies are checked against
this module by `backend/tests/unit/test_footprint_root.py`. The values are
chosen here and nowhere else.

For how long
------------

A place is also a lease: it has a term, three states, and — on the way out —
three receipts (结论 24、39；不变量 I19). Both halves answer the same question
about one borrowed machine, which is why they sit together: one says what of
ours is on it, the other says until when, and what has to be back in our hands
before it stops being ours.
"""

import asyncio
import base64
import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.domain.agent.platform_failures import (
    LEASE_EXPIRED_CODE,
    LEASE_EXPIRED_MESSAGE,
)
from app.domain.device.supply import Supply

_ROOT = ".cheese"

# The directory the platform installed into inside a session home before the
# root moved. Session homes only: see the module docstring.
_PREVIOUS_SESSION_DIR = ".claude"


def footprint_root() -> str:
    """The one directory the platform writes under a machine's own `$HOME`."""
    return _ROOT


def session_platform_dirs() -> tuple[str, ...]:
    """The platform's directories inside a SESSION home, current one first."""
    return (_ROOT, _PREVIOUS_SESSION_DIR)


# What a session's own home is called from the machine's side, and the one
# directory inside it the platform stages files into. The checkout the agent
# works in is that home's `room/` (`device_provider._work_dir`), so a name
# chosen here is a name chosen NOT to be in it.
#
# The sandbox CLI carries a copy (`backend/sandbox/cheese`): `cheese library
# get` writes its default here rather than into the work tree, and the two
# copies are held together by `tests/unit/test_footprint_root.py`. A file 芝士
# fetches for itself and a file the platform staged for it are the same kind of
# file and belong in the same directory.
STAGED_DIR = "attachments"

#: The checkout, relative to a session's home — **the name is chosen here**, and
#: the code that creates the directory reads it from here. That is the whole
#: point: a rule that names the directory it excludes is only checkable while
#: the name it excludes and the name the checkout actually gets are the same
#: string. Declared beside the rule and read by nobody who makes the directory,
#: it would go on rejecting `room/` after a rename while waving the real
#: checkout through.
#:
#: `device_provider._work_dir` builds the path the backend hands a machine.
#: Three programs run ON the machine with nothing of ours importable — the
#: executor bootstrap, the cleanup script and the session transfer — so they
#: carry copies, held to this one by `tests/unit/test_footprint_root.py`, the
#: same way they already carry the footprint root.
#:
#: `write` rejects it as a SEGMENT anywhere under the footprint, not just at the
#: one place a session's own checkout sits, so that a `home` already pointing
#: into a checkout is refused too. It errs toward refusing: a staged file whose
#: own name carried a `room/` segment would be turned away. That name cannot
#: occur — an attachment is addressed `uploads/<hex>/<filename>` or
#: `library/<filename>` and a filename has no slash in it (`routes/topics.py`
#: strips one) — and refusing an image is a lost image, while accepting one into
#: a repository is a file its owner did not add and we do not remove.
CHECKOUT_DIR = "room"


class OutsideFootprint(RuntimeError):
    """A platform write aimed somewhere the platform does not own.

    Raised rather than logged: a write that lands outside the footprint is
    either a file `cheese uninstall` will walk past, or — the case this exists
    for — a file in the hosted checkout, which the platform does not write into
    at all (结论 49，不变量 I21b).
    """


async def write(
    data: bytes,
    *,
    home: str,
    name: str,
    hub,
    device_id: str,
    screen: str,
    execution_target: dict | None = None,
    timeout: float = 30,
) -> str:
    """Put one file the platform owns on a machine it borrows, and say where.

    **The only call in `app/` that sends server-held bytes to a machine as a
    file** — an upload, an attachment, anything whose content came from outside
    and has to arrive whole — over either transport that carries file bytes:
    `file.put` on the connector, `stage_file` on the executor. That is not
    tidiness: 结论 49 says the platform's own things — its configuration, hooks,
    skills, prompts, progress, memories, drafts, backups — never enter the
    hosted checkout, in the tree or as an untracked file beside it, and a rule
    about where writes land can only be checked where the writes are. One entry
    point makes it one assertion (below) and one AST count
    (`tests/unit/test_platform_writes_nothing_into_checkout.py`) rather than an
    argument about every path some caller builds at runtime.

    It is not the only way `app/` makes a file appear on a machine, and reading
    it as that is how somebody ends up writing into the checkout believing it
    impossible. The platform also lays down its own programs by shipping shell
    to `hub.exec`: the launch script (`device_provider._ship_launcher`), the
    hook forwarder and the per-turn forwarded token
    (`device_provider._screen_file_refresh`), the toolchain, the hook and the
    `cheese` CLI that the launch script writes (`machine_launcher`). Every one
    of those destinations is a path THIS module names — under
    `footprint_root()` — which is the platform's own directory and is what 结论
    49 allows. None of them is countable from an AST: the destination sits
    inside a shell string assembled at runtime, which is exactly the
    cross-process path analysis the guard was written to stop pretending it can
    do. What holds that half honest is the acceptance that runs a real turn and
    then reads the checkout's own porcelain status
    (`scripts/remote_execution/private_terminal.py --ordinary`).

    `home` is the session's home on that machine, `$HOME`-anchored. The staged
    file goes beside the checkout, never inside it: the checkout is that home's
    `room/`, and an uploaded image dropped in there is an untracked file in
    somebody's repository that they never put there and we never take away.

    **One write, to the filesystem the agent actually reads.** A screen with an
    executor is reached through it, because that is where the agent runs — a
    private chat's executor is a container, and a file written on the host it
    sits on is a file the agent cannot open. Only a screen with no executor is
    written to over the connector. Doing both, which is what this replaces, put
    the same bytes at the same path twice for every room executor, and hid the
    fact that the two transports resolve a path against different homes.

    Returns the absolute path **as the machine reports it** — the backend cannot
    expand that machine's `$HOME`, and the agent is handed this path verbatim.
    """
    # Two spellings of one destination, because the two transports that reach a
    # machine do not share a `$HOME`. The connector runs as the machine's owner,
    # so it is handed the `$HOME`-anchored path and expands it against that home.
    # The executor was launched with `HOME` set to the session's own home, so it
    # is handed the path relative to that and resolves it against `Path.home()`.
    # Handing either one the other's spelling writes a real file in a real
    # directory that nothing will ever look in.
    relative = f"{STAGED_DIR}/{name}"
    destination = f"{home}/{relative}"
    root = f"$HOME/{footprint_root()}/"
    if not destination.startswith(root):
        raise OutsideFootprint(
            f"{destination} is outside $HOME/{footprint_root()}, which is the "
            "whole of what the platform writes on a machine"
        )
    # The leading slash is what makes this a SEGMENT test rather than a prefix
    # one: without it the first segment under the root carries no slash in front
    # of it, and a `home` of `$HOME/.cheese/room/…` — a checkout sitting
    # directly under the footprint — would walk straight through.
    if f"/{CHECKOUT_DIR}/" in "/" + destination.removeprefix(root):
        raise OutsideFootprint(
            f"{destination} is inside the hosted checkout, which the platform "
            "does not write into (结论 49)"
        )
    if execution_target:
        from app.domain.agent import private_chat

        # `timeout` is the CALLER's budget, and both transports owe it the same
        # answer. `private_chat.control` takes no timeout of its own and spends
        # 660s inside — the right ceiling for a turn, eleven minutes too long
        # for a file whose caller said the message rides on without it. The one
        # caller stages images one at a time, so a wedged executor would hold
        # somebody's message for that budget once per image.
        answer = await asyncio.wait_for(
            private_chat.control(
                execution_target,
                {
                    "subtype": "stage_file",
                    "path": relative,
                    "data": base64.b64encode(data).decode(),
                },
                hub=hub,
            ),
            timeout,
        )
    else:
        answer = await hub.put_file(
            device_id, screen, destination, data, timeout=timeout
        )
    return str(answer["path"])


# --- 租约：一个地点的使用权 ----------------------------------------------------
#
# 地点是**有期限、可归还的租约**（结论 24）。今天一台机器在代码里只有「在用」和
# 「没了」两种样子，于是「闲置停机」和「归还」是同一个动作，而归还要跑的三张收据
# 也就成了每次闲置都要跑一遍的东西。三态把这两件事分开：
#
#   在用 (``in_use``)    —— 手在这条会话上，期限由 ``Lease.expires_at`` 说。
#   休眠 (``asleep``)    —— 只有平台自己开的机器给得出：快照后停机，reconnect
#                           window 之内醒来是同一台机器、工作区原样。**休眠不是
#                           归还**，这条路上一张收据都不取（结论 39）。
#   已归还 (``returned``)—— 使用权结束，机器可以被销毁或还给它的主人。走到这里
#                           之前三张收据一张不能少（不变量 I19）。
#
# 租约挂在**会话**上，不在房间上，也不在 ``devices`` 上（结论 60）：一个房间里的
# 两个队友各租各的手，各自迁移互不影响。落点是 P18 已经加好的
# ``agent_sessions.work_lease``——把 ``lease_until`` / ``state`` 加到 ``devices``
# 会立刻造出第二份声明。

_LEASE_STATE = "lease_state"
_LEASE_EXPIRES_AT = "lease_expires_at"
_LEASE_ASLEEP_UNTIL = "lease_asleep_until"


class LeaseState(enum.StrEnum):
    """一份使用权的三态。"""

    in_use = "in_use"
    asleep = "asleep"
    returned = "returned"


#: 这一份手就是跑会话进程的那台机器——工具不经执行器再跳一程。它说的是物理事实，
#: 不是通道的类名：``compute.py`` 读它来决定哪些通道上挂得住一个把进程和工作区放
#: 在同一台机器上的骨架。
HANDS_HERE = "hands_here"
#: 这个地点给得出租约的第三态（结论 39）。只有平台自己开的机器给得出——别人的
#: 机器不是平台停得了的。
CAN_SLEEP = "can_sleep"
#: 平台开的机器，所以平台有权销毁它；人接入的机器平台只能停止使用（结论 24，以及
#: ``Supply`` 自己的 docstring）。
PLATFORM_MAY_DESTROY = "platform_may_destroy"


def capabilities_of(supply: Supply, *, hands_here: bool) -> frozenset[str]:
    """一个地点的能力位——**由物理事实推出来，不是各家自己报一份**。

    两件事决定全部三位：这台机器是谁开的（``supply``），以及这条通道上的手是不是
    就在跑会话进程的那台机器上。名字不进这个函数：一张按类名维护的能力表注定是
    「写下它那天恰好有这个本事的通道」的清单，下一个学会的永远不会被加进去——
    ``Channel.builds_model_env`` 那段注释讲的就是这件事，而 ``compute.py`` 的
    ``isinstance(c, DeviceChannel)`` 是它的完成态：读起来像一条能力规则，因为两个
    子类都继承了 ``DeviceChannel``，实际恒为真，什么都没排除。
    """
    bits = {HANDS_HERE} if hands_here else set()
    if supply is Supply.cloud:
        bits |= {CAN_SLEEP, PLATFORM_MAY_DESTROY}
    return frozenset(bits)


@dataclass(frozen=True, slots=True)
class Lease:
    """一条会话对一个地点的使用权。

    ``expires_at`` 是**期限的唯一来源**：没有第二个各自为政的超时常量，到期只有
    ``LeaseExpired`` 一种表达，而那一条是送到 agent 面前的**事件**，平台不据此替
    它换机器（结论 55、不变量 I25）。``None`` = 无期限。

    ``asleep_until`` 只在 ``asleep`` 这一档有值：reconnect window 的尽头。window
    之内醒来是同一台机器、工作区原样；过了才转成归还，那时三张收据一张不少。
    """

    machine: str
    resource_id: str
    state: LeaseState = LeaseState.in_use
    expires_at: datetime | None = None
    asleep_until: datetime | None = None

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def wakeable(self, now: datetime) -> bool:
        """还在 reconnect window 之内——醒来是同一台机器，不是新开一台。"""
        return self.state is LeaseState.asleep and (
            self.asleep_until is None or now < self.asleep_until
        )

    @classmethod
    def from_record(cls, record: dict | None) -> "Lease | None":
        """``agent_sessions.work_lease`` 那一行读成一份租约。

        ``None``（手就在会话机上的那些会话，比如私聊）读成「没有租约」。有行而没
        记过状态的读成「在用、无期限」——那是这一列在本 PR 之前写下的每一行的意思。
        """
        if not record:
            return None
        return cls(
            machine=record.get("device_id") or "",
            resource_id=record.get("resource_id") or "",
            state=LeaseState(record.get(_LEASE_STATE, LeaseState.in_use)),
            expires_at=_read_moment(record.get(_LEASE_EXPIRES_AT)),
            asleep_until=_read_moment(record.get(_LEASE_ASLEEP_UNTIL)),
        )

    def into(self, record: dict) -> dict:
        """把三态与期限写回那一行，别的字段原样留给写它的人。

        地点那一行还带着执行器装到了哪一步（它自己的 ``state`` 键），所以租约的
        状态另起一个名字——两个 ``state`` 挤在一个 dict 里，先写的那个会被后写的
        那个悄悄顶掉。
        """
        written = dict(record)
        written[_LEASE_STATE] = self.state.value
        written[_LEASE_EXPIRES_AT] = _write_moment(self.expires_at)
        written[_LEASE_ASLEEP_UNTIL] = _write_moment(self.asleep_until)
        return written


def _read_moment(value: object) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _write_moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class LeaseExpired(Exception):
    """期限到了——**唯一的一种表达**。

    它是一条事件，不是一次工具报错：``failure_code`` 让它落成平台事件
    ``lease_expired``，agent 的下一轮输入里读到的是一句能力话，不是一个 HTTP 状态
    码。平台**不据此换资源**：换不换机器是 agent 的判断，它手上有「开一台机器 /
    换到 Cloud」那两个工具（结论 40、55）。
    """

    failure_code = LEASE_EXPIRED_CODE

    def __init__(self, lease: Lease) -> None:
        self.lease = lease
        super().__init__(LEASE_EXPIRED_MESSAGE)


@dataclass(frozen=True, slots=True)
class Receipts:
    """归还之前必须拿到的三张（不变量 I19）。

    三张都是**已经发生过的事**的凭据，不是待办：transcript 已经落到平台库里、记忆
    整理已经跑过、未提交的工作已经推上去了。一张不齐就保持 pending 并说明理由——
    机器一删，缺的那一张就再也补不回来。
    """

    transcript_stored: bool = False
    memory_tidied: bool = False
    work_published: bool = False

    def missing(self) -> tuple[str, ...]:
        absent = []
        if not self.transcript_stored:
            absent.append("transcript 还没落库")
        if not self.memory_tidied:
            absent.append("记忆整理还没跑")
        if not self.work_published:
            absent.append("还有没推上去的工作")
        return tuple(absent)


def receipts_ready(entering: LeaseState, receipts: Receipts) -> tuple[bool, str]:
    """能不能走到 ``entering`` 这一档，以及走不了的理由。

    所有回收路径（归档清理、Cloud 释放、容器回收、会话机 drain）走这一个函数，而
    不是各自抄一段散文。**休眠不取收据**（结论 39）：把休眠也套上收据，等于每次闲
    置都跑一遍记忆整理，而休眠期间那三样东西一样都没有离开那台机器。
    """
    if entering is not LeaseState.returned:
        return True, ""
    absent = receipts.missing()
    if absent:
        return False, "回收前的收据没齐：" + "、".join(absent)
    return True, ""


class Place(Protocol):
    """一个地点：一双可以租、可以还、（有的）可以睡的手。

    上游读 ``capabilities()``，不读类名——这是本接口存在的全部理由。三个实现的差别
    只允许长在这张能力表上：``DeviceChannel``（你接入的机器）、``CloudChannel``
    （平台开的机器，唯一给得出第三态的那个）、``CentralChannel``（会话进程在中心
    机、手在执行机上，所以它没有 ``HANDS_HERE``）。
    """

    def capabilities(self) -> frozenset[str]: ...

    async def acquire(self, term: timedelta | None) -> Lease: ...

    async def release(self, lease: Lease) -> Receipts: ...

    async def sleep(self, lease: Lease) -> Lease: ...

    async def wake(self, lease: Lease) -> Lease: ...
