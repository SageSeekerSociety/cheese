"""芝士 writes every part of the publish form, and is told what the page it
was asked on is for."""

from zoneinfo import ZoneInfo

import pytest

from app.core.errors import BadRequestError
from app.domain.task import drafts
from app.domain.task.models import TaskDraft

SPACE = drafts.Space(
    id=7,
    name="系统与网络实验室",
    categories={},
    tags={},
    zone=ZoneInfo("Asia/Shanghai"),
    mentors={"wangli": "王立", "chenhao": "陈浩"},
    domain_groups={"本校邮箱": 3, "合作单位": 4},
)


def test_mentors_are_named_by_name_or_by_handle():
    fields = drafts.from_agent({"mentors": ["王立", "@chenhao", "王立"]}, SPACE, {})

    assert fields["mentors"] == ["wangli", "chenhao"]


def test_only_the_writer_and_the_space_admins_may_be_mentors():
    with pytest.raises(BadRequestError) as refused:
        drafts.from_agent({"mentors": ["李四"]}, SPACE, {})

    assert "王立（wangli）" in str(refused.value)


def test_access_is_the_domain_groups_named_or_everyone():
    limited = drafts.from_agent({"access": ["本校邮箱"]}, SPACE, {})
    everyone = drafts.from_agent({"access": []}, SPACE, {})

    assert limited["accessControlEnabled"] is True
    assert limited["accessDomainGroupIds"] == [3]
    assert everyone["accessControlEnabled"] is False
    assert everyone["accessDomainGroupIds"] == []
    with pytest.raises(BadRequestError):
        drafts.from_agent({"access": ["外校"]}, SPACE, {})


def test_registration_locking_guidance_and_attachments_land_in_their_fields():
    fields = drafts.from_agent(
        {
            "registration_start": "2026-11-01 08:00",
            "team_locking": "报名通过后锁定",
            "ai_guidance": "先让学生写清需求再写代码",
            "attachments": [12],
        },
        SPACE,
        {},
        [{"id": 12, "name": "课题说明.pdf", "size": 2048}],
    )

    assert fields["registrationStartAt"] == 1793491200000
    assert fields["teamLockingPolicy"] == "LOCK_ON_APPROVAL"
    assert fields["teaching"] == {"systemPrompt": "先让学生写清需求再写代码"}
    assert fields["teachingOwn"] is True
    assert fields["attachmentIds"] == [12]
    assert fields["attachmentFiles"] == [
        {"id": 12, "name": "课题说明.pdf", "size": 2048}
    ]


def test_empty_guidance_goes_back_to_the_space_default():
    fields = drafts.from_agent({"ai_guidance": ""}, SPACE, {})

    assert fields["teaching"] == {}
    assert fields["teachingOwn"] is False


def test_a_draft_says_its_new_fields_in_芝士s_words():
    row = TaskDraft(
        id=5,
        user_id=1,
        space_id=7,
        version=2,
        fields={
            "name": "eBPF 延迟分析",
            "submitterType": "TEAM",
            "teamLockingPolicy": "LOCK_ON_APPROVAL",
            "mentors": ["wangli"],
            "accessControlEnabled": True,
            "accessDomainGroupIds": [3],
            "attachmentFiles": [{"id": 12, "name": "课题说明.pdf", "size": 1}],
        },
    )

    view = drafts.agent_view(row, SPACE)

    assert "team_locking: 报名通过后锁定" in view
    assert "mentors: 王立（wangli）" in view
    assert "access: 本校邮箱" in view
    assert "课题说明.pdf（文件 id 12）" in view
    assert "可以当导师的人：王立（wangli）、陈浩（chenhao）" in view


def test_a_draft_nothing_is_written_in_says_so_and_takes_the_first_task():
    row = TaskDraft(
        id=9,
        user_id=1,
        space_id=7,
        version=1,
        fields={"submitterType": "USER", "submissionSchema": []},
    )

    assert "新题目，尚未填写" in drafts.agent_view(row, SPACE)
    assert "第一道题写进它" in drafts.scene(row)

    row.fields = {**row.fields, "name": "课题 A"}
    assert "尚未填写" not in drafts.agent_view(row, SPACE)


def test_the_edit_page_changes_the_task_it_is_open_on():
    row = TaskDraft(id=9, user_id=1, space_id=7, task_id=88, version=1, fields={})

    told = drafts.scene(row, "Lab 3 缓存模拟器")

    assert "修改题目页" in told and "「Lab 3 缓存模拟器」" in told
    assert "不新建草稿" in told
