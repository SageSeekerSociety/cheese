"""#282 四轴中的两根：**供给形式**与**可见性**。

这两个枚举是词汇，不是存储。放在这里而不是 ``repository.py``，是因为别的领域
（``machine``、``agent``）要拿 ``Supply`` 判「这台机器平台能不能动」——而
``tests/unit/test_domain_import_guard.py`` 那道守卫不许跨领域 import 对方的
repository 模块，且那道守卫是对的：值类型不该住在数据访问层里，否则想读一个枚举
就得先捅穿一层。

``repository.py`` 从这里 re-export，域内老调用点不用改。
"""

import enum

from app.core.sentences import NoticeText, say

__all__ = [
    "Supply",
    "Visibility",
    "default_visibility",
    "sandbox_unavailable",
]


class Supply(enum.StrEnum):
    """How this machine came to be ours (#282 四轴 · 供给形式).

    THE axis that decides what the platform may DO to a machine — not whether it
    is a container or a VM. The platform opened it on demand → it may destroy it;
    a human enrolled a machine they already had → it may not. Every disposal rule
    is a consequence of this one field, which is why it is stored rather than
    inferred: `ProjectMachine.device_id` can reverse-look-up the same fact today,
    and a semantics that exists only by reverse lookup is the bug #282 is about.

    Consequence: the SAME physical VM is `cloud` when the platform provisions it
    and `self_hosted` when someone enrolls a box they keep running — 入口决定待遇，
    不是硬件决定待遇. MicroCloud needs no special case; it just enters both ways.
    """

    cloud = "cloud"  # platform-provisioned → disposable, replaceable, reclaimable
    self_hosted = "self_hosted"  # human-enrolled → the platform may only stop USING it


class Visibility(enum.StrEnum):
    """What a turn on this machine can see and touch (#282 四轴 · 可见性).

    Orthogonal to `Supply` — the axis that keeps "I want to see the host" from
    dragging "and never reclaim me" along with it. `host` is not merely "can look
    at the box": a device screen runs `claude --dangerously-skip-permissions` as
    the owner (see agent.device_launch), so it can read and write every other
    room's worktree on that machine and exec into their containers.

    `isolated` runs each session's executor in a sandbox of its own
    (`remote_execution/bootstrap.sandbox_argv`), on Cloud machines and enrolled
    ones alike. It is the default everywhere (#2320); `host` is the 档 a
    machine's owner picks for a room on purpose. Whether a given enrolled
    machine can give a session a sandbox depends on what it runs
    (`sandbox_unavailable`), and one that cannot refuses an `isolated` room
    rather than running it over the whole machine.
    """

    isolated = "isolated"  # one sandbox per session — blast radius is the session
    # The whole machine, as its owner: chosen by that owner for a room, never a
    # default (`topics_compute.set_topic_compute_profile`).
    host = "host"


def default_visibility() -> Visibility:
    """The 档 a new binding gets when nobody picked one — every binding point
    asks this, so the picker, the room badge and the session's sandbox cannot
    disagree about it.

    `isolated` on every machine (#2320): a Cloud machine and an enrolled one
    both give each session a sandbox of its own, so the session sees its own
    directory and its project's package store, and neither the machine owner's
    files (the connector's credential is among them) nor other sessions'."""
    return Visibility.isolated


# The connector systems with no isolated environment yet, and the sentence a
# room there is refused with. Which environment a system gets is
# `bootstrap.sandbox_argv`'s to build: bubblewrap on Linux. A system gains one
# by leaving this table in the same change that teaches that function to build
# it. Windows is meant to get it through WSL, whose connector is a Linux one.
_NO_SANDBOX = {
    "darwin": "sandboxUnavailableMacos",
    "windows": "sandboxUnavailableWindows",
}


def sandbox_unavailable(target: str) -> NoticeText | None:
    """Why an enrolled machine whose connector build is ``target``
    (``<os>-<arch>``, as its `hello` names it) cannot give a session an
    isolated environment, or None when it can or the build is not known yet.

    An `isolated` room there is refused with this sentence rather than run
    over the whole machine; the machine's owner can give the room full machine
    access instead. The install on the machine refuses the same way for a
    machine this side has not heard from (`bootstrap.sandbox_argv`)."""
    key = _NO_SANDBOX.get(target.split("-", 1)[0])
    return say(key) if key is not None else None
