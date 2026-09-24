"""A place: what of ours is on a machine we borrow, and what it can do.

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

Six programs cannot ask this module and carry the names themselves: the
environment runner, the cleanup script, the executor bootstrap, the session
transfer and the sandbox CLI (`backend/sandbox/cheese`) all run where nothing of
ours is importable — two are piped in on stdin and have no `__file__` to look
at, one is written out beside the room's own files and run as a script, one is
exec'd out of a string, one is shipped into the agent's container as a
standalone program — and the connector is Go. Their copies are checked against
this module by `backend/tests/unit/test_footprint_root.py`. The values are
chosen here and nowhere else.

What it can do
--------------

The same module answers the other question about that borrowed machine: what
this place gives out. The capability a place can be asked for is named here, so
that the one physical fact behind it — are the hands the very machine the
session process runs on — is what upstream reads, and nothing has to ask a
channel which class it is (结论 24).
"""

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


# The directory inside a session's home that files fetched for the agent go
# into. The checkout the agent works in is that home's `room/`
# (`device_provider._work_dir`), so a name chosen here is a name chosen NOT to
# be in it.
#
# The sandbox CLI carries a copy (`backend/sandbox/cheese`): `cheese library
# get` writes its default here rather than into the work tree, and the two
# copies are held together by `tests/unit/test_footprint_root.py`.
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
CHECKOUT_DIR = "room"


# --- 能力位：这个地点给得出什么 ------------------------------------------------
#
# 上游读能力位，不读类名。一张按类名维护的能力表注定是「写下它那天恰好有这个本事
# 的通道」的清单，下一个学会的永远不会被加进去 —— ``Channel.builds_model_env`` 那
# 段注释讲的就是这件事，而 ``compute.py`` 的 ``isinstance(c, DeviceChannel)`` 是它
# 的完成态：读起来像一条能力规则，实际上两个子类都继承了 ``DeviceChannel``，它恒为
# 真。

#: 这一份手就是跑会话进程的那台机器——工具不经执行器再跳一程。它说的是物理事实，
#: 不是通道的类名：``compute.py`` 读它来决定哪些通道上挂得住一个把进程和工作区放
#: 在同一台机器上的骨架 (pi)。
# 今天这张表上只有这一位，因为今天只有这一件事是上游真的在问的。多的那几位（平台
# 能不能销毁这台机器、这个地点给不给得出「休眠」这第三态）跟着**真正实现它们的那个
# PR** 一起出生 —— 一位没有读者的能力位，和一张按类名写死的表一样，都是「写下它那天
# 的样子」，区别只是它还骗人说这里已经有一个可以问的接口。同理，推它的那一行就写在
# 唯一读物理事实的地方（``Channel.capabilities``）：今天只有一个读者，第二种地点要
# 报一张不一样的表的那天，再把推导收成一个函数。
HANDS_HERE = "hands_here"
