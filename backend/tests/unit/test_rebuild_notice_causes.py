"""每一次强制重建都要在房间里说一声，而且说的得是真话。

`_ensure_container` 有三条会 `docker rm -f` 的路径，`_ensure_session` 另有一条
会 `tmux kill-session`。它们的共同后果是：交互会话和所有后台任务当场没了，而
**除了这条通知以外没有任何东西会告诉用户**。

改之前有两个洞：

* **令牌失效那条完全不通知。** 它是 #334 加的，加进了「要不要重建」的判断，却
  没加进「要不要说」的判断——于是后端一重启，话题的 session 悄无声息地没了。
* **另外三条都说「镜像已切换」。** 对其中两条（env 漂移、CLI 挂载过期）是假话。

现在容器按房间分配，两类后果的**范围不一样**，通知也必须分开说：拆容器会把
同房间每个话题一起带走，杀会话只影响这一个话题。说错范围和说错原因一样坏。

所以下面测的不是「有没有发消息」，是「发的那条说的是不是这次真实发生的事」。
"""

import uuid

import pytest

from app.domain.agent.sandbox_notices import REBUILD_CAUSE_TEXT, rebuild_notice_text
from app.domain.agent.tmux_provider import TmuxHooksProvider

TOPIC = uuid.uuid4()
IMAGE = "the-current-image"


def _provider() -> TmuxHooksProvider:
    return TmuxHooksProvider(image=IMAGE)


async def _false() -> bool:
    return False


async def _true() -> bool:
    return True


def _wire(
    monkeypatch,
    *,
    said: list[tuple[uuid.UUID, str]],
    created: list[str],
    removed: list[str],
    cur_image: str = IMAGE,
    stamp_drift: bool = False,
    cli_stale: bool = False,
    exists: bool = True,
) -> None:
    async def _docker(*args: str):
        if args[0] == "inspect" and "{{.Config.Image}}" in args:
            return (0, cur_image, "") if exists else (1, "", "no such object")
        if args[0] == "inspect" and "Labels" in " ".join(args):
            return 0, "whatever-stamp", ""
        if args[0] == "rm":
            removed.append(args[-1])
        return 0, "", ""

    async def _create(name: str, env: dict, anchor) -> None:
        created.append(name)

    async def _notice(topic_id: uuid.UUID, cause: str = "image") -> None:
        said.append((topic_id, cause))

    monkeypatch.setattr("app.domain.agent.tmux_provider._docker", _docker)
    monkeypatch.setattr(TmuxHooksProvider, "_create_container", staticmethod(_create))
    monkeypatch.setattr(
        TmuxHooksProvider,
        "_cli_mount_stale",
        staticmethod(lambda *a: _true() if cli_stale else _false()),
    )
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider.env_stamp_drifted",
        lambda *a: stamp_drift,
    )
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider.warn_container_rebuilt", _notice
    )


ENV = {"SBX_SESSIONS": "/tmp/sessions", "CHEESE_API": "http://backend"}
ROOM = uuid.uuid4()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "cause"),
    [
        ({"cur_image": "some-older-image"}, "image"),
        ({"stamp_drift": True}, "env"),
        ({"cli_stale": True}, "cli_mount"),
    ],
)
async def test_each_cause_is_named_accurately(monkeypatch, kwargs, cause):
    """It used to say 「镜像已切换」 for all of these — a lie in two cases out of
    three, and the kind of lie that sends someone to check the wrong thing."""
    said: list[tuple[uuid.UUID, str]] = []
    _wire(monkeypatch, said=said, created=[], removed=[], **kwargs)

    await _provider()._ensure_container(TOPIC, ROOM, ENV)

    assert said == [(TOPIC, cause)]
    assert REBUILD_CAUSE_TEXT[cause] in rebuild_notice_text(cause)


def test_a_box_rebuild_and_a_session_restart_say_different_things():
    """The two consequences differ in SCOPE, and a notice that overstates or
    understates it is the reason someone goes looking in the wrong place: a box
    rebuild takes every topic in the room, a session restart takes one."""
    box_text = rebuild_notice_text("image")
    session_text = rebuild_notice_text("token")
    assert "沙箱容器已重建" in box_text
    assert "交互会话已重启" in session_text
    assert "同房间其他话题不受影响" in session_text
    assert "同房间其他话题不受影响" not in box_text


@pytest.mark.anyio
async def test_a_healthy_box_is_neither_rebuilt_nor_announced(monkeypatch):
    """The overwhelmingly common path. A notice that fires every turn is a
    notice everyone stops reading."""
    said: list[tuple[uuid.UUID, str]] = []
    created: list[str] = []
    removed: list[str] = []
    _wire(monkeypatch, said=said, created=created, removed=removed)

    await _provider()._ensure_container(TOPIC, ROOM, ENV)

    assert (said, created, removed) == ([], [], [])


@pytest.mark.anyio
async def test_a_first_ever_creation_says_nothing(monkeypatch):
    """Nothing was destroyed, so there is nothing to apologise for — announcing
    a rebuild here would tell the user their session died on the turn that
    created it."""
    said: list[tuple[uuid.UUID, str]] = []
    created: list[str] = []
    _wire(monkeypatch, said=said, created=created, removed=[], exists=False)

    await _provider()._ensure_container(TOPIC, ROOM, ENV)

    assert created, "the box still has to be built"
    assert said == []


def test_an_unregistered_cause_still_produces_a_sentence():
    """A future rebuild trigger that forgets to register its wording must
    degrade to a vaguer notice, never to silence."""
    text = rebuild_notice_text("something-nobody-registered")
    assert "已重建" in text
    assert "都被终止了" in text
