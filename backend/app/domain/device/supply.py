"""#282 四轴中的两根：**供给形式**与**可见性**。

这两个枚举是词汇，不是存储。放在这里而不是 ``repository.py``，是因为别的领域
（``machine``、``agent``）要拿 ``Supply`` 判「这台机器平台能不能动」——而
``tests/unit/test_domain_import_guard.py`` 那道守卫不许跨领域 import 对方的
repository 模块，且那道守卫是对的：值类型不该住在数据访问层里，否则想读一个枚举
就得先捅穿一层。

``repository.py`` 从这里 re-export，域内老调用点不用改。
"""

import enum

__all__ = ["Supply", "Visibility", "has_runnable_transport"]


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

    `isolated` has NO transport behind it yet (every device screen today is
    `host`). It is stored so a future per-room-container device backend is a new
    value, not a new column — but nothing may hand it out until that lands.
    """

    isolated = "isolated"  # one container per room — blast radius is the room
    host = "host"  # the whole machine, as its owner — 申请制, never a default


def has_runnable_transport(visibility: Visibility) -> bool:
    """Whether the device backend has a transport for this visibility today."""
    return visibility is Visibility.host
