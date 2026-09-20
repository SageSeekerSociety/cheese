"""一条事件「关于什么」，就决定它落在哪（结论 14，不变量 I10）。

三行封闭表，三条断言：卡的事落卡、房间的事落房间时间线、项目的事落项目总览。
第四组断言守的是这张表**封闭**这件事：说不清关于什么的那几种组合被拒绝，而不是
悄悄落到一个能写得进去的地方——落错房间的事件不会报错，只会再也读不到。
"""

import uuid

import pytest

from app.domain.block.about import EventAbout, Landing, landing

PROJECT = uuid.uuid4()
ROOM = uuid.uuid4()
TASK = uuid.uuid4()
OVERVIEW = uuid.uuid4()


def test_a_card_event_lands_on_the_card():
    assert landing(
        EventAbout.task, project_id=PROJECT, room_id=ROOM, task_id=TASK
    ) == Landing(project_id=PROJECT, topic_id=ROOM, task_id=TASK)


def test_a_room_event_lands_on_the_room_timeline():
    assert landing(EventAbout.room, project_id=PROJECT, room_id=ROOM) == Landing(
        project_id=PROJECT, topic_id=ROOM, task_id=None
    )


def test_a_project_event_lands_on_the_overview():
    assert landing(
        EventAbout.project, project_id=PROJECT, overview_room_id=OVERVIEW
    ) == Landing(project_id=PROJECT, topic_id=OVERVIEW, task_id=None)


def test_the_overview_is_not_just_any_room():
    """项目的事只认总览那个房间：`room_id` 给了也不作数。

    合成一个参数的那一版里，「项目的事」等于「调用点随手给的房间」，
    这张表就不封闭了。
    """
    with pytest.raises(ValueError):
        landing(EventAbout.project, project_id=PROJECT, room_id=ROOM)


@pytest.mark.parametrize(
    "about,ids",
    [
        # 卡的事拿不出卡号：落到房间时间线上是悄悄落错地方。
        (EventAbout.task, {"room_id": ROOM}),
        (EventAbout.task, {"task_id": TASK}),
        # 房间的事带着卡号：那它关于的是那张卡，不是房间。
        (EventAbout.room, {"room_id": ROOM, "task_id": TASK}),
        (EventAbout.project, {"overview_room_id": OVERVIEW, "task_id": TASK}),
        (EventAbout.room, {}),
    ],
)
def test_ids_that_do_not_match_what_the_event_is_about_are_refused(about, ids):
    with pytest.raises(ValueError):
        landing(about, project_id=PROJECT, **ids)
