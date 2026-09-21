"""模型绑在活上，卡上显示的是它真花出去的那个。

四条判据（不变量 I17）在这里各有落点，其余两条在 integration：

  ① 改一条活的绑定，下一轮生效而当前轮不变    → 今天没有落点，见 integration
  ② 房间主线的模型在一轮里改不动              → integration
  ③ 卡上显示的模型 = 这条活最后一行用量的模型，而 `tasks` 上没有一列存它
  ④ 启动环境里没有任何 `*_MODEL` 键，也没有 `CLAUDE_MODEL` → test_device_provider.py
"""

import uuid

import pytest

from app.core.errors import ValidationError
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION
from app.domain.room_task import presentation
from app.domain.room_task.binding import catalog, resolve
from app.domain.room_task.models import Task


def _work(model: str | None = None, effort: str | None = None) -> Task:
    """一条活，窄到只有这一层读的两列。"""
    return Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        title="一条活",
        model=model,
        effort=effort,
    )


# —— 次序：这条活的绑定 → 项目默认 ————————————————————————————————


def test_a_room_has_no_binding_so_it_runs_on_the_project_default():
    """房间主线不是一条活，它永远走项目默认（结论 3：主线程不换模型，缓存一直热）。"""
    assert resolve(None, catalog(None)).model == "sonnet"
    assert resolve(None, catalog(None)).supply == SUBSCRIPTION


def test_a_work_without_a_binding_of_its_own_runs_on_the_project_default():
    assert resolve(_work(), catalog(None)).model == "sonnet"


def test_a_work_runs_on_what_it_is_bound_to():
    bound = resolve(_work(model="opus"), catalog(None))
    assert bound.model == "opus"
    assert bound.supply == SUBSCRIPTION


def test_the_binding_carries_the_supply_that_serves_it():
    """id 上看不出走哪个池 —— 订阅模型的 id 是 `opus` 这种短名。目录说了算。"""
    assert resolve(_work(model="glm-5.2"), catalog(None)).supply == GATEWAY


def test_the_project_default_follows_the_projects_own_supply():
    """项目挑了网关池，默认就是网关池那一个，不是订阅的 sonnet。"""
    bound = resolve(None, catalog({"supply": "gateway"}))
    assert bound.supply == GATEWAY
    assert bound.model != "sonnet"


def test_effort_rides_with_the_work_not_with_the_project():
    assert resolve(_work(model="opus", effort="high"), catalog(None)).effort == "high"
    assert resolve(None, catalog(None)).effort is None


# —— 答不出就拒绝，不换池（I27） ————————————————————————————————


def test_a_model_this_project_cannot_use_is_refused_by_name():
    """静默退回项目默认就是换池：屏幕上写着一个，跑的是另一个。"""
    with pytest.raises(ValidationError, match="no-such-model"):
        resolve(_work(model="no-such-model"), catalog(None))


# —— ③ 卡上显示的模型从用量算，不存状态列 ————————————————————————


def test_a_card_shows_the_model_the_work_actually_spent_on():
    """花过就写它真花的那个 —— 哪怕绑的是另一个。"""
    task = _work(model="opus")
    assert (
        presentation.card_model(task, spent="claude-sonnet-5", choices=catalog(None))
        == "sonnet"
    )


def test_spending_on_a_model_does_not_rename_it_on_the_card():
    """同一个模型，花钱前后卡上是同一个字。

    绑定说的是目录 id（`sonnet`），用量行记的是真发出去的名字
    （`claude-sonnet-5`）。两边各吐各的，这张卡就会在第一次请求之后自己换个名字，
    而模型根本没动。
    """
    unspent = presentation.card_model(
        _work(model="sonnet"), spent=None, choices=catalog(None)
    )
    spent = presentation.card_model(
        _work(model="sonnet"), spent="claude-sonnet-5", choices=catalog(None)
    )
    assert unspent == spent == "sonnet"


def test_a_model_the_catalog_cannot_name_is_shown_as_it_was_spent():
    """目录反查不出来的，照原样写 —— 显示真花在谁身上，好过显示一个猜的短名。"""
    shown = presentation.card_model(
        _work(model="opus"), spent="claude-sonnet-5-20260929", choices=catalog(None)
    )
    assert shown == "claude-sonnet-5-20260929"


def test_what_a_card_shows_is_written_in_no_column_of_the_row():
    """③ 的守卫：显示出来的那个值，在这一行的任何一列里都找不到。

    一列存「显示什么」要靠每次真实用量去刷新它，而它对不上的那天，卡上写着 A、
    账单上是 B，没有任何地方说得出是谁写错的。
    """
    task = _work(model="opus")
    shown = presentation.card_model(
        task, spent="claude-sonnet-5", choices=catalog(None)
    )
    stored = {
        column.name: getattr(task, column.name) for column in Task.__table__.columns
    }
    assert shown not in stored.values()


def test_a_card_that_has_spent_nothing_shows_what_it_is_bound_to():
    shown = presentation.card_model(
        _work(model="opus"), spent=None, choices=catalog(None)
    )
    assert shown == "opus"


def test_a_card_with_neither_shows_the_project_default():
    shown = presentation.card_model(_work(), spent=None, choices=catalog(None))
    assert shown == "sonnet"


def test_an_unpriced_row_does_not_get_to_say_which_model_ran():
    """`spent=None` 是「没有一行说得出模型」，不是「没花过」—— 退回绑定。"""
    shown = presentation.card_model(
        _work(model="opus"), spent="", choices=catalog(None)
    )
    assert shown == "opus"


def test_a_card_renders_a_binding_the_project_can_no_longer_use():
    """绑坏了照原样写出来 —— 拒绝是执行路径的事，不是渲染的事。

    项目把供给从订阅改成网关，或者运维从目录里摘掉一个型号，先前绑上去的那批活
    立刻就解析不出来。这一屏是唯一能看见、进而改掉这条绑定的地方。
    """
    shown = presentation.card_model(
        _work(model="no-such-model"), spent=None, choices=catalog(None)
    )
    assert shown == "no-such-model"
