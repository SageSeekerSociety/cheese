"""冷启动看门狗：一轮**从没开口**的 turn 不该占满整个上限。

2026-08-12 的平台级故障就是这个形状。每个话题的 turn 都在同一秒开始，
`tools=0`、`first_output_s=None`，然后各自安安静静地跑满自己的天花板
（tmux 后端是 900 秒）才失败——而整整那 900 秒里，话题在界面上写着「进行中」。

从 runner 这一层看，「运行环境没起来」和「模型在深思」长得一模一样：都是沉默。
所以判据不能是「跑了多久」，只能是「**开没开过口**」。第一个 `tool` /
`assistant_block` 一到，这层就整个退场，慢的 turn 一秒都不会被砍。

下面四条把这个契约钉住：短保险丝只覆盖开口前、开过口就换成真上限、
`turn_ceiling` **不能**顶掉保险丝、以及关掉之后行为逐字回到从前。
"""

import asyncio
import uuid

import pytest

from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker


class _Backend:
    """够用的 chat service 替身。`submit` 收的那个对象既产 frame 又落系统事件，
    所以这里也是一个对象扮两个角色（和 test_turn_continuation 的 `_Quiet` 同形）。

    子类只需要覆写 `frames()`。
    """

    def __init__(self) -> None:
        self.events: list[str] = []
        # 平台提示统一契约: 房间里的一行是 `text`，展开才看的长文在 meta.detail。
        self.notices: list[str] = []

    def frames(self):
        raise NotImplementedError

    async def converse(self, **_):
        async for frame in self.frames():
            yield frame

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        self.events.append(text)
        self.notices.append(text + ((meta or {}).get("detail") or ""))
        return {"id": "b1", "content": text}


class _Mute(_Backend):
    """运行环境没起来的样子：一个 frame 都不产出，直到被砍。

    注意它连 `user_block` 都不发——真实的冷启动失败里卡住的是容器本身，
    压根走不到发第一帧。
    """

    async def frames(self):
        await asyncio.sleep(30)
        yield {"type": "done"}  # pragma: no cover


class _MuteButAnnouncesItsCeiling(_Backend):
    """先报一个很高的天花板，然后一样什么都不产出。

    这正是踩过的坑：`chat.py` 在**碰容器之前**就发 `turn_ceiling`，所以这一帧
    只证明「选中了某个后端」，不证明任何东西起来了。如果它能顶掉保险丝，
    看门狗对真实故障就恰好失效——因为真实故障里这一帧总是会来。
    """

    async def frames(self):
        yield {"type": "turn_ceiling", "seconds": 900}
        await asyncio.sleep(30)
        yield {"type": "done"}  # pragma: no cover


class _SpeaksThenHangs(_Backend):
    """开了口，然后卡住——这是另一种失败，不归看门狗管。"""

    async def frames(self):
        yield {"type": "assistant_block", "text": "在看了"}
        await asyncio.sleep(30)
        yield {"type": "done"}  # pragma: no cover


async def _error_frame(runner: AgentWorkRunner, backend: _Backend) -> dict:
    """跑一轮，返回它最终那条 error frame。"""
    topic = uuid.uuid4()
    async with runner._broker.subscribe(str(topic)) as q:
        runner.submit(backend, topic, author="u", content="hi", summon=True)
        while True:
            frame = await asyncio.wait_for(q.get(), 10)
            if frame["type"] == "error":
                return frame


@pytest.mark.anyio
async def test_a_turn_that_never_speaks_is_cut_at_the_fuse_not_at_the_ceiling():
    backend = _Mute()
    # 上限 10 秒，保险丝 0.05 秒。保险丝没生效的话这一轮要跑满 10 秒，
    # 下面 2 秒的等待会先超时——「被上限砍」和「被保险丝砍」就是这么分开的。
    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=10.0, first_output_timeout_s=0.05
    )
    frame = await asyncio.wait_for(_error_frame(runner, backend), 2)

    assert "一个字都没输出" in frame["message"]
    # 而且**不能**说「已完成的改动都在」——什么都没跑，那句话是假的。
    assert "已完成的改动都在" not in frame["message"]
    # 「按运行环境没起来处理」和那一串常见原因收进了展开区，房间里只剩一行。
    assert backend.notices and "运行环境" in backend.notices[0]


@pytest.mark.anyio
async def test_turn_ceiling_alone_does_not_lift_the_fuse():
    # 这条是整个改动里最容易写错的一处。`turn_ceiling` 是碰容器之前发的，
    # 让它把 deadline 推到 900 秒，等于把看门狗对真实故障关掉。
    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=10.0, first_output_timeout_s=0.05
    )
    frame = await asyncio.wait_for(
        _error_frame(runner, _MuteButAnnouncesItsCeiling()), 2
    )

    assert "一个字都没输出" in frame["message"]


@pytest.mark.anyio
async def test_first_output_retires_the_fuse_so_a_slow_turn_runs_its_full_ceiling():
    # 开过口的 turn 归上限管：保险丝 0.05 秒、上限 0.6 秒，它必须活过前者、
    # 死在后者，报的也得是老那条超时话术。
    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=0.6, first_output_timeout_s=0.05
    )
    frame = await asyncio.wait_for(_error_frame(runner, _SpeaksThenHangs()), 3)

    assert "超时被中断" in frame["message"]
    assert "一个字都没输出" not in frame["message"]


@pytest.mark.anyio
async def test_the_fuse_can_be_turned_off():
    # 0 = 关掉。留这个口子是因为判据是启发式的，真出误杀要能一键回到从前——
    # 关掉之后连话术都必须逐字是旧的那条。
    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=0.3, first_output_timeout_s=0
    )
    frame = await asyncio.wait_for(_error_frame(runner, _Mute()), 3)

    assert "超时被中断" in frame["message"]
    assert "一个字都没输出" not in frame["message"]


# --- #388 缺陷一: known-expired credential → fast-fail + the TRUE reason ---------
# The 10-hour outage was a live `claude` 401'd on every message while the fuse
# fired every 300s and the event blamed "容器/磁盘/网络" — three guesses, when the
# real reason (an expired subscription credential) was already known to the
# backend. When it IS known, the fuse must be cut short AND the event must say so.


@pytest.mark.anyio
async def test_known_expired_credential_fast_fails_with_the_true_reason():
    # first_output_timeout_s is LARGE (30s) but the credential is known-expired, so
    # the credential fuse (0.05s) is what fires — proving the short-circuit is the
    # credential signal, not a small generic fuse. If it did NOT fire, the 30s wall
    # would blow past the 2s wait below.
    runner = AgentWorkRunner(
        InProcessBroker(),
        turn_timeout_s=60.0,
        first_output_timeout_s=30.0,
        credential_expiry_of=lambda _topic: 0,  # epoch → long expired
        credential_expired_fuse_s=0.05,
    )
    frame = await asyncio.wait_for(_error_frame(runner, _Mute()), 2)

    # The event tells the truth: an expired subscription credential needing host
    # re-auth — NOT the misleading container/disk/network guesses.
    assert "凭据已过期" in frame["message"]
    assert "重新认证" in frame["message"]
    assert "运行环境" not in frame["message"]
    # And it does not promise an auto-retry — retrying burns another fuse on the
    # same dead credential.
    assert "会自动再试一次" not in frame["message"]
    assert frame.get("code") == "subscription_credential_expired"


@pytest.mark.anyio
async def test_a_live_credential_keeps_the_generic_cold_start_message():
    # Credential lookup reports a healthy (far-future) expiry → the credential path
    # never engages, and a mute turn falls to the ordinary cold-start message.
    runner = AgentWorkRunner(
        InProcessBroker(),
        turn_timeout_s=10.0,
        first_output_timeout_s=0.05,
        credential_expiry_of=lambda _topic: 10**12,  # year 33658 — very much alive
        credential_expired_fuse_s=0.05,
    )
    frame = await asyncio.wait_for(_error_frame(runner, _Mute()), 2)

    assert "一个字都没输出" in frame["message"]
    assert "凭据已过期" not in frame["message"]
    assert frame.get("code") is None


@pytest.mark.anyio
async def test_no_credential_lookup_leaves_the_fuse_untouched():
    # The default (no lookup wired) must behave exactly as before: a mute turn is
    # the generic cold-start failure, no credential branch anywhere.
    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=10.0, first_output_timeout_s=0.05
    )
    frame = await asyncio.wait_for(_error_frame(runner, _Mute()), 2)

    assert "一个字都没输出" in frame["message"]
    assert "凭据已过期" not in frame["message"]
