"""#282 四轴中的两根：**供给形式**与**可见性**。

这两个枚举是词汇，不是存储。放在这里而不是 ``repository.py``，是因为别的领域
（``machine``、``agent``）要拿 ``Supply`` 判「这台机器平台能不能动」——而
``tests/unit/test_domain_import_guard.py`` 那道守卫不许跨领域 import 对方的
repository 模块，且那道守卫是对的：值类型不该住在数据访问层里，否则想读一个枚举
就得先捅穿一层。

``repository.py`` 从这里 re-export，域内老调用点不用改。
"""

import enum

__all__ = [
    "Supply",
    "Visibility",
    "binding_visibility",
    "has_runnable_transport",
]


class Supply(enum.StrEnum):
    """How this machine came to be ours (#282 四轴 · 供给形式).

    THE axis that decides what the platform may DO to a machine — not whether it
    is a container or a VM. The platform opened it on demand → it may destroy it;
    a human enrolled a machine they already had → it may not. Every disposal rule
    is a consequence of this one field, which is why it is stored rather than
    inferred: `CloudHost.device_id` can reverse-look-up the same fact today,
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
    (`remote_execution/bootstrap.sandbox_argv`). Only Cloud machines have that
    transport today; on a self-hosted one it has none, and nothing may hand it
    out there until #2320 step 2 lands (`has_runnable_transport`).
    """

    isolated = "isolated"  # one sandbox per session — blast radius is the session
    # The whole machine, as its owner. 申请制 by intent — but it IS the default
    # on self-hosted machines today, because `isolated` has no transport there
    # and there is nothing else to default to. `default_visibility()` says so
    # out loud rather than leaving the comment and the code disagreeing; it
    # stops being the default the moment #2320 step 2 lands.
    host = "host"


def has_runnable_transport(visibility: Visibility, supply: Supply) -> bool:
    """Whether the device backend can run a turn at this visibility on a
    machine of this supply today: `host` anywhere, `isolated` only on Cloud
    machines, the ones that sandbox each session."""
    return visibility is Visibility.host or supply is Supply.cloud


# Most conservative first: the default is the first entry that can actually run.
_VISIBILITY_PREFERENCE = (Visibility.isolated, Visibility.host)


def default_visibility() -> Visibility:
    """The 档 a topic on a self-hosted machine gets when nobody picked one.

    DERIVED from what has a transport, never declared, because the two used to be
    declared separately and disagreed: the market catalogue advertised `isolated`
    as the default while `resolve_pinned_device` bound `host` unconditionally. A
    person opening the picker was told their topic was boxed; every topic in fact
    had whole-machine access. Both surfaces now read this, so when step 2 (#2320)
    gives `isolated` a transport on self-hosted machines, the default moves in
    both places at once and nobody has to remember the second one.
    """
    for visibility in _VISIBILITY_PREFERENCE:
        if has_runnable_transport(visibility, Supply.self_hosted):
            return visibility
    raise RuntimeError("no visibility has a runnable transport")


def binding_visibility(supply: Supply) -> Visibility:
    """一条新绑定拿到的可见性档——**四个绑定点共用的那一个答案**。

    以前四个绑定点各写一个字面量 `host`，于是「这个档默认是什么」在代码里有四份声
    明，而 `default_visibility()` 只被市场目录和自动挑机那两处读到。人在选择器上看
    到的和绑定时写下的因此可以不一样，而且没有任何东西会说出来。

    档由供给决定，不由调用点决定：

    * 平台开的机器上，每条会话的执行器跑在自己的沙箱里（#2320），所以是
      `isolated`：会话只看得见自己的目录和本项目的包缓存，看不见机器的主人目录
      （连接器的凭据在那里）和别的会话。
    * 人接入的机器上，答案是 `default_visibility()`：从「哪个档今天真有传输层」推
      出来。#2320 第二步给那里的 `isolated` 接上传输层的那天，它和市场目录一起移动。
    """
    if supply is Supply.cloud:
        return Visibility.isolated
    return default_visibility()
