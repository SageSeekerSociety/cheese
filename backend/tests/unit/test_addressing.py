"""寻址是一个纯函数：一条事件点到了谁，只有这一份判据（结论 13、14、15；I11）。

一张参数化表，两句断言反复用：

- **下一步在平台手上，收件人为空。** 平台自己在推进的事不惊动任何人 —— 结论 15
  收紧「一律告诉人」的那一半。
- **转到某个参与者手上那一刻，恰好一次。** 一个人一条，重复点名不变成两条。

最后一组是族 4 里被结论 14、15 根除的那 7 个 PR：它们各自修的那件事，在这张表里
是一行，往后再犯就当场红。
"""

import pytest

from app.domain.delivery.addressing import (
    NOBODY,
    REASON_ASKED,
    REASON_REPORTER,
    REASON_REVIEWER,
    Event,
    Hand,
    Recipient,
    address,
    hand_of,
)
from app.domain.room_task.presentation import Column

# —— 下一步在平台手上：谁都不通知 ————————————————————————————————


@pytest.mark.parametrize(
    "event",
    [
        Event(),
        Event(reviewers=("alice",)),
        Event(reporter="bob"),
        Event(asked="carol"),
        Event(reviewers=("alice",), reporter="bob", asked="carol"),
    ],
    ids=["空事件", "点了验收人", "点了提需求的人", "点了被问的人", "三种都点了"],
)
def test_nobody_is_told_while_the_next_step_is_the_platforms(event):
    """连名字都点了也不发 —— 这一档在结构上拿不出收件人。"""
    assert address(event, Hand.platform) == NOBODY


# —— 转到参与者手上：恰好一次 ——————————————————————————————————


@pytest.mark.parametrize(
    "event,expected",
    [
        (
            Event(reviewers=("alice",)),
            (Recipient("alice", REASON_REVIEWER),),
        ),
        (
            Event(reporter="bob"),
            (Recipient("bob", REASON_REPORTER),),
        ),
        (
            Event(asked="carol"),
            (Recipient("carol", REASON_ASKED),),
        ),
        (
            Event(reviewers=("alice",), reporter="bob"),
            (Recipient("alice", REASON_REVIEWER), Recipient("bob", REASON_REPORTER)),
        ),
        (
            Event(reviewers=("alice", "dave")),
            (Recipient("alice", REASON_REVIEWER), Recipient("dave", REASON_REVIEWER)),
        ),
    ],
    ids=["验收人", "提需求的人", "被问的人", "递卡点到两个人", "卡加被作废的那一票"],
)
def test_each_named_participant_is_told_exactly_once(event, expected):
    assert address(event, Hand.participant).recipients == expected


def test_one_person_in_two_roles_is_one_recipient():
    """自己给自己报的需求：一个人，一条，理由留强的那个。"""
    addressed = address(Event(reviewers=("alice",), reporter="alice"), Hand.participant)
    assert addressed.recipients == (Recipient("alice", REASON_REVIEWER),)


def test_the_question_outranks_the_card():
    """待确认问题挡住其余所有事，所以同一个人先是「被问的那个人」。"""
    addressed = address(
        Event(reviewers=("alice",), reporter="alice", asked="alice"), Hand.participant
    )
    assert addressed.recipients == (Recipient("alice", REASON_ASKED),)


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_name_is_not_a_recipient(blank):
    """空的提需求人是「这条活没人报」，不是一个叫空字符串的人。"""
    assert address(Event(reporter=blank), Hand.participant) == NOBODY


def test_addressing_the_same_event_twice_gives_the_same_answer():
    """纯函数：重算不会变成第二次打扰 —— 「恰好一次」靠的就是这一条。"""
    event = Event(reviewers=("alice",), reporter="bob")
    assert address(event, Hand.participant) == address(event, Hand.participant)


def test_reason_for_answers_only_about_the_people_it_named():
    addressed = address(Event(reviewers=("alice",), reporter="bob"), Hand.participant)
    assert addressed.reason_for("alice") == REASON_REVIEWER
    assert addressed.reason_for("bob") == REASON_REPORTER
    assert addressed.reason_for("carol") is None
    assert addressed.handles == ("alice", "bob")


# —— 看板那一列说的就是下一步在谁手上 ——————————————————————————


@pytest.mark.parametrize(
    "column,hand",
    [
        (Column.building, Hand.platform),
        (Column.delivering, Hand.platform),
        (Column.needs_you, Hand.participant),
        (Column.done, Hand.platform),
        (Column.archived, Hand.platform),
    ],
)
def test_the_board_column_and_the_next_hand_are_one_answer(column, hand):
    assert hand_of(column) is hand


def test_every_column_is_in_the_table():
    """封闭表：看板多一列而这里没跟上，必须当场缺一行，不能悄悄落进「不通知」。"""
    for column in Column:
        assert hand_of(column) in (Hand.platform, Hand.participant)


# —— 族 4：被结论 14、15 根除的那 7 个 PR，一个一行 ——————————————


@pytest.mark.parametrize(
    "event,next_hand,expected",
    [
        # #1128 一台机器这一分钟关着，平台在等它 —— 那是一个没人在用的平台的常态。
        # 当时五条一分钟、来自五个话题，一直发到机器开机为止。
        (Event(), Hand.platform, ()),
        # #1063 房间的运行环境还在准备，平台自己在等 —— 等待本身不是要谁动手。
        (Event(), Hand.platform, ()),
        # #1058 闸门收走了房间的屏幕，卡停在那儿等验收人。
        (
            Event(reviewers=("alice",)),
            Hand.participant,
            (Recipient("alice", REASON_REVIEWER),),
        ),
        # #1105 后台任务崩了，下一步在人手上，而当时的通知数是 0。
        (
            Event(reporter="bob"),
            Hand.participant,
            (Recipient("bob", REASON_REPORTER),),
        ),
        # #1081 一个人看到的报错要到能修它的人手上，同样是一条也没发出去过。
        (
            Event(reporter="bob"),
            Hand.participant,
            (Recipient("bob", REASON_REPORTER),),
        ),
        # #1055 一轮失败了，发起那一轮的人在等这个答复。
        (
            Event(asked="carol"),
            Hand.participant,
            (Recipient("carol", REASON_ASKED),),
        ),
        # #1132 站点挂了 —— 提需求的人等的东西有了结果（坏结果），通知他一次。
        (
            Event(reporter="bob"),
            Hand.participant,
            (Recipient("bob", REASON_REPORTER),),
        ),
    ],
    ids=["#1128", "#1063", "#1058", "#1105", "#1081", "#1055", "#1132"],
)
def test_the_family_4_regressions(event, next_hand, expected):
    assert address(event, next_hand).recipients == expected
