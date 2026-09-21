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

What it can do
--------------

The same module answers the other question about that borrowed machine: what
this place gives out. One physical fact — are the hands the very machine the
session process runs on — decided in one place, so that nothing upstream has to
ask a channel which class it is (结论 24).
"""

import asyncio
import base64

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
HANDS_HERE = "hands_here"


def capabilities_of(*, hands_here: bool) -> frozenset[str]:
    """一个地点的能力位——**由物理事实推出来，不是各家自己报一份**。

    今天这张表上只有一位，因为今天只有一件事是上游真的在问的。多的那几位（平台能
    不能销毁这台机器、这个地点给不给得出「休眠」这第三态）要跟着**真正实现它们的
    那个 PR** 一起出生：一位没有读者的能力位，和一张按类名写死的表一样，都是「写下
    它那天的样子」，区别只是它还骗人说这里已经有一个可以问的接口。
    """
    return frozenset({HANDS_HERE}) if hands_here else frozenset()
