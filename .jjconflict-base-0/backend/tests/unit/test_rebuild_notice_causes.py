"""每一次强制重建都要在房间里说一声，而且说的得是真话。

`_ensure_container` 有四条会 `docker rm -f` 的路径。它们的共同后果是：交互会话
和所有后台任务当场没了，而**除了这条通知以外没有任何东西会告诉用户**。

改之前有两个洞：

* **令牌失效那条完全不通知。** 它是 #334 加的，加进了「要不要重建」的判断，却
  没加进「要不要说」的判断——于是后端一重启，话题的 session 悄无声息地没了。
* **另外三条都说「镜像已切换」。** 对其中两条（env 漂移、CLI 挂载过期）是假话。

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
    token_dead: bool = False,
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

    async def _create(name: str, env: dict) -> None:
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
        TmuxHooksProvider,
        "_hook_token_dead",
        staticmethod(lambda *a: _true() if token_dead else _false()),
    )
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider.env_stamp_drifted",
        lambda *a: stamp_drift,
    )
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider.warn_container_rebuilt", _notice
    )


ENV = {"SBX_SESSION": "/tmp/session", "CHEESE_API": "http://backend"}


@pytest.mark.anyio
async def test_a_dead_token_rebuild_is_announced(monkeypatch):
    """The defect. #334 taught `_ensure_container` to rebuild a deaf box but not
    to say so, so the topic lost its session with no message at all."""
    said: list[tuple[uuid.UUID, str]] = []
    created: list[str] = []
    removed: list[str] = []
    _wire(monkeypatch, said=said, created=created, removed=removed, token_dead=True)

    await _provider()._ensure_container(TOPIC, ENV)

    assert removed and created, "a deaf box must still be rebuilt"
    assert said == [(TOPIC, "token")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "cause"),
    [
        ({"cur_image": "some-older-image"}, "image"),
        ({"stamp_drift": True}, "env"),
        ({"cli_stale": True}, "cli_mount"),
        ({"token_dead": True}, "token"),
    ],
)
async def test_each_cause_is_named_accurately(monkeypatch, kwargs, cause):
    """It used to say 「镜像已切换」 for all of these — a lie in three cases out
    of four, and the kind of lie that sends someone to check the wrong thing."""
    said: list[tuple[uuid.UUID, str]] = []
    _wire(monkeypatch, said=said, created=[], removed=[], **kwargs)

    await _provider()._ensure_container(TOPIC, ENV)

    assert said == [(TOPIC, cause)]
    assert REBUILD_CAUSE_TEXT[cause] in rebuild_notice_text(cause)


@pytest.mark.anyio
async def test_a_healthy_box_is_neither_rebuilt_nor_announced(monkeypatch):
    """The overwhelmingly common path. A notice that fires every turn is a
    notice everyone stops reading."""
    said: list[tuple[uuid.UUID, str]] = []
    created: list[str] = []
    removed: list[str] = []
    _wire(monkeypatch, said=said, created=created, removed=removed)

    await _provider()._ensure_container(TOPIC, ENV)

    assert (said, created, removed) == ([], [], [])


@pytest.mark.anyio
async def test_a_first_ever_creation_says_nothing(monkeypatch):
    """Nothing was destroyed, so there is nothing to apologise for — announcing
    a rebuild here would tell the user their session died on the turn that
    created it."""
    said: list[tuple[uuid.UUID, str]] = []
    created: list[str] = []
    _wire(monkeypatch, said=said, created=created, removed=[], exists=False)

    await _provider()._ensure_container(TOPIC, ENV)

    assert created, "the box still has to be built"
    assert said == []


def test_an_unregistered_cause_still_produces_a_sentence():
    """A future rebuild trigger that forgets to register its wording must
    degrade to a vaguer notice, never to silence."""
    text = rebuild_notice_text("something-nobody-registered")
    assert "已重建" in text
    assert "都被终止了" in text
