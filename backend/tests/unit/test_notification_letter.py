"""通知邮件读起来要单独站得住：说清发生了什么、原话、在哪儿，按钮直达那件事。

收件人在自己的邮箱里读它，那里没有平台的任何上下文 —— 一封只写「你有一条新通知」
的信等于什么也没说。
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.domain.notification import maintenance
from app.domain.notification.letter import letter_for, render_html, render_text
from app.domain.notification.models import NotificationType
from app.domain.notification.push import push_link, push_text
from app.domain.team.models import Team
from app.domain.user.models import User, UserProfile

pytestmark = pytest.mark.anyio

ROOM = {"projectId": "p-1", "topicId": "t-1", "topicTitle": "登录页改版"}


def test_cheese_question_leads_with_the_question_and_lands_on_it():
    letter = letter_for(
        {
            "type": "CHEESE_QUESTION",
            "payload": {**ROOM, "question": "要保留旧的登录入口吗？", "blockId": "b-9"},
        }
    )
    body = render_html(letter)

    assert "要保留旧的登录入口吗？" in letter.subject
    assert "要保留旧的登录入口吗？" in body
    assert "登录页改版" in body
    assert f"{settings.frontend_url}/projects/p-1/topics/t-1?block=b-9" in body


def test_the_letter_about_a_question_follows_it_into_the_task_it_was_asked_in():
    """问在任务里的题：按钮落到那个任务，不是频道主线。

    那条消息在任务自己的线上，频道主线上没有它 —— 落到频道，人点开就是一句「这条
    消息已不存在」（推送、站内通知的链接读的是同一条规则 `push.push_link`）。
    """
    letter = letter_for(
        {
            "type": "CHEESE_QUESTION",
            "payload": {
                **ROOM,
                "question": "这个字段存毫秒还是秒？",
                "blockId": "b-9",
                "taskId": "k-2",
            },
        }
    )

    assert f"{settings.frontend_url}/projects/p-1/topics/t-1/tasks/k-2?block=b-9" in (
        render_html(letter)
    )


def test_room_notice_carries_the_sentence_said_in_the_room():
    letter = letter_for(
        {"type": "ROOM_NOTICE", "payload": {**ROOM, "content": "验收卡等你确认"}}
    )
    assert letter.subject == "验收卡等你确认"
    assert "验收卡等你确认" in render_html(letter)


def test_device_in_use_says_who_works_on_which_machine():
    body = render_html(
        letter_for(
            {
                "type": "DEVICE_IN_USE",
                "payload": {
                    **ROOM,
                    "projectName": "官网",
                    "agentName": "小橙",
                    "deviceName": "MacBook Pro",
                    "machineAccess": True,
                },
            }
        )
    )
    assert "小橙 开始在「MacBook Pro」上工作" in body
    assert "官网" in body
    assert "能访问整台机器" in body


def test_mention_quotes_what_was_said():
    letter = letter_for(
        {"type": "MENTION", "payload": {"excerpt": "@你 帮忙看下这个接口"}}
    )
    assert "@你 帮忙看下这个接口" in render_html(letter)
    # 讨论指不到房间，按钮落到收件箱，那里一定列着这一条。
    assert letter.link == f"{settings.frontend_url}/inbox"


def test_a_mention_in_a_channel_says_who_and_where_and_lands_on_the_message():
    letter = letter_for(
        {
            "type": "MENTION",
            "payload": {
                **ROOM,
                "authorName": "Alice",
                "content": "帮忙看下这个接口",
                "blockId": "b-3",
                "threadId": "th-1",
            },
        }
    )
    body = render_html(letter)

    assert letter.subject == "Alice 在「登录页改版」提到了你"
    assert "帮忙看下这个接口" in body
    assert letter.link == (
        f"{settings.frontend_url}/projects/p-1/topics/t-1/threads/th-1?block=b-3"
    )


def test_a_reply_in_a_thread_says_who_replied_where():
    letter = letter_for(
        {
            "type": "THREAD_REPLY",
            "payload": {**ROOM, "authorName": "Bob", "content": "方案一"},
        }
    )
    assert letter.subject == "Bob 在「登录页改版」的支线里回复了"
    assert "方案一" in render_html(letter)


def test_an_unknown_type_still_reads_as_a_sentence():
    letter = letter_for({"type": "SOMETHING_NEW", "payload": {}})
    assert letter.subject == "你在芝士上有一条新通知"
    assert letter.link == f"{settings.frontend_url}/inbox"


def test_a_credit_warning_leads_to_the_page_it_can_be_fixed_on():
    letter = letter_for(
        {
            "type": "CREDIT_WARNING",
            "payload": {
                "content": "团队额度快用完了：方案额度还剩 100 点，11月1日重置。",
                "layer": "team",
                "teamHandle": "b-tree",
            },
        }
    )
    assert "团队额度快用完了" in letter.subject
    assert letter.link == f"{settings.frontend_url}/teams/b-tree/credits"

    project = letter_for(
        {
            "type": "CREDIT_WARNING",
            "payload": {
                "content": "这个项目的额度预算快用完了：还剩 100 点。",
                "event": "credit_warning",
                "layer": "project",
                "projectId": "p-1",
            },
        }
    )
    assert project.link == f"{settings.frontend_url}/projects/p-1/settings/environment"


def test_the_email_escapes_user_text_and_preserves_the_destination():
    body = render_html(
        letter_for(
            {
                "type": "ROOM_NOTICE",
                "payload": {
                    "content": "<script>bad()</script>",
                    "projectId": "p-1",
                    "topicId": "room-1",
                    "topicTitle": "Review",
                },
            }
        )
    )
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert f"{settings.frontend_url}/projects/p-1/topics/room-1" in body


def test_the_plain_text_part_has_everything_the_html_says():
    text = render_text(
        letter_for(
            {"type": "TEAM_JOIN_REQUEST", "payload": {"message": "我想一起做前端"}},
            {"requester": "张三", "team": "前端组"},
        )
    )
    assert "张三申请加入「前端组」" in text
    assert "> 我想一起做前端" in text
    assert f"{settings.frontend_url}/inbox" in text


async def test_a_team_email_names_the_person_and_the_team(db_factory, monkeypatch):
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]
    async with db_factory() as session:
        owner = User(
            username=f"owner-{suffix}",
            email=f"owner-{suffix}@example.com",
            created_at=now,
            updated_at=now,
        )
        requester = User(
            username=f"req-{suffix}",
            email=f"req-{suffix}@example.com",
            created_at=now,
            updated_at=now,
        )
        session.add_all([owner, requester])
        await session.flush()
        session.add(
            UserProfile(
                user_id=requester.id,
                nickname="张三",
                intro="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
        )
        team = Team(
            name="前端组",
            handle=f"t-{suffix}",
            intro="intro",
            description="description",
            avatar_id=1,
            created_at=now,
            updated_at=now,
        )
        session.add(team)
        await session.commit()
        owner_id, requester_id, team_id = owner.id, requester.id, team.id

    sender = AsyncMock()
    sender.send.return_value = True
    monkeypatch.setattr(maintenance, "get_email_sender", lambda: sender)

    await maintenance.send_email(
        db_factory,
        {
            "recipientId": owner_id,
            "type": "TEAM_JOIN_REQUEST",
            "payload": {
                "requester": {"type": "user", "id": str(requester_id)},
                "team": {"type": "team", "id": str(team_id)},
                "message": "我想一起做前端",
            },
        },
    )

    sent = sender.send.await_args.kwargs
    assert sent["subject"] == "张三申请加入「前端组」"
    assert "我想一起做前端" in sent["body_html"]
    assert "张三申请加入「前端组」" in sent["body_text"]


def test_a_question_mail_calls_the_teammate_by_its_name():
    letter = letter_for(
        {
            "type": "CHEESE_QUESTION",
            "payload": {**ROOM, "question": "先做哪一页？", "agentName": "Nova"},
        }
    )
    assert letter.subject == "Nova 问你：先做哪一页？"
    assert "芝士" not in letter.subject + letter.eyebrow


def test_a_mention_in_a_projects_own_document_names_the_document_and_opens_it():
    """文档正文里的 @：说是哪份文档、那一行原话，按钮落到那份文档
    （它不在任何频道里）。"""
    payload = {
        "projectId": "p-1",
        "documentId": "d-7",
        "documentTitle": "接口约定",
        "author": "alice",
        "authorName": "李老师",
        "content": "<@bob> 周五前定下分页参数",
    }
    letter = letter_for({"type": "MENTION", "payload": payload})

    assert "接口约定" in letter.subject and "李老师" in letter.subject
    assert "周五前定下分页参数" in render_html(letter)
    assert f"{settings.frontend_url}/projects/p-1/docs/d-7" in render_html(letter)
    assert push_link(payload) == "/projects/p-1/docs/d-7"
    title, body = push_text(NotificationType.MENTION, payload, "zh-CN")
    assert "接口约定" in title and "李老师" in title
    assert "周五前定下分页参数" in body
