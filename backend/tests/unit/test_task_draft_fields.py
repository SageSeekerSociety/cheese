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
            "team_locking": "申请通过后锁定",
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


def test_guidance_adds_to_what_the_task_has_and_names_the_way_of_helping():
    current = {"teaching": {"materialIds": [3], "systemPrompt": "旧的"}}
    fields = drafts.from_agent(
        {"ai_mode": "先明确需求再实现", "ai_guidance": ""}, SPACE, current
    )

    assert fields["teaching"] == {
        "materialIds": [3],
        "systemPrompt": None,
        "mode": "spec",
    }
    assert fields["teachingOwn"] is True

    cleared = drafts.from_agent(
        {"ai_mode": "沿用上层设置", "ai_guidance": ""}, SPACE, {}
    )
    assert cleared["teachingOwn"] is False


def test_a_draft_says_its_new_fields_in_芝士s_words():
    row = TaskDraft(
        id=5,
        user_id=1,
        space_id=7,
        version=2,
        fields={
            "name": "eBPF 延迟分析",
            "submitterType": "TEAM",
            "startMode": "APPLY",
            "teamLockingPolicy": "LOCK_ON_APPROVAL",
            "mentors": ["wangli"],
            "accessControlEnabled": True,
            "accessDomainGroupIds": [3],
            "attachmentFiles": [{"id": 12, "name": "课题说明.pdf", "size": 1}],
        },
    )

    view = drafts.agent_view(row, SPACE, private=True)

    assert "team_locking: 申请通过后锁定" in view
    assert "mentors: 王立（wangli）" in view
    assert "access: 本校邮箱" in view
    assert "课题说明.pdf（files/12）" in view
    assert "可以当导师的人：王立（wangli）、陈浩（chenhao）" in view


def test_stages_are_written_on_the_persons_clock_and_items_name_their_stage():
    """A due date is on the person's clock, as the task's deadline is. A stage
    the task has keeps its id; a new one gets an id at once, so items and lines
    — which 芝士 points at a stage by its id or its name — carry the stage's id
    and follow it however the stages are later reordered."""
    fields = drafts.from_agent(
        {
            "stages": [
                {"name": "需求分析", "description": "交需求", "due": "2026-11-01"},
                {"id": "a1b2c3", "name": "实现"},
            ],
            "submission": [
                {"name": "需求文档", "kind": "文字", "stage": "需求分析"},
                {"name": "代码", "kind": "文件", "stage": "a1b2c3"},
            ],
            "rubric": [{"item": "正确", "points": 60, "stage": "实现"}],
        },
        SPACE,
        {},
    )

    assert [s["name"] for s in fields["stages"]] == ["需求分析", "实现"]
    # The last minute of 1 November in Shanghai.
    assert fields["stages"][0]["dueAt"] == 1793548740000
    assert fields["stages"][1]["dueAt"] is None
    new_id = fields["stages"][0]["id"]
    assert new_id and fields["stages"][1]["id"] == "a1b2c3"
    assert [e["stage"] for e in fields["submissionSchema"]] == [new_id, "a1b2c3"]
    assert fields["rubric"][0]["stage"] == "a1b2c3"

    view = drafts.agent_view(
        TaskDraft(id=5, user_id=1, space_id=7, version=1, fields=fields),
        SPACE,
        private=True,
    )
    assert f"  1. 需求分析（id: {new_id}）｜截止 2026-11-01 23:59｜交需求" in view
    assert "  - 代码（文件）｜阶段 实现（a1b2c3）" in view
    assert "  2. 实现（id: a1b2c3）｜截止 不设" in view


def test_a_draft_says_which_items_name_a_stage_it_does_not_have():
    row = TaskDraft(
        id=6,
        user_id=1,
        space_id=7,
        version=1,
        fields={
            "name": "分阶段",
            "descriptionMarkdown": "要求",
            "submitterType": "USER",
            "rank": 2,
            "stages": [{"id": "a1", "name": "一"}],
            "submissionSchema": [{"prompt": "代码", "type": "FILE", "stage": None}],
            "rubric": [{"item": "正确", "points": 1, "stage": "gone"}],
        },
    )

    # No stage is the first stage's; a stage the draft does not have is not.
    assert drafts.missing(row) == ["rubric"]


def test_two_stages_of_one_name_cannot_be_named_by_it():
    with pytest.raises(BadRequestError) as refused:
        drafts.from_agent(
            {
                "stages": [{"name": "实验"}, {"name": "实验"}],
                "rubric": [{"item": "正确", "points": 60, "stage": "实验"}],
            },
            SPACE,
            {},
        )

    assert refused.value.args and "实验" in str(refused.value)


def test_an_item_naming_a_stage_the_draft_does_not_have_is_refused():
    with pytest.raises(BadRequestError) as refused:
        drafts.from_agent(
            {
                "stages": [{"id": "a1b2c3", "name": "实现"}],
                "rubric": [{"item": "正确", "points": 60, "stage": 2}],
            },
            SPACE,
            {},
        )

    assert "实现（a1b2c3）" in str(refused.value)


def test_a_draft_nothing_is_written_in_says_so_and_takes_the_first_task():
    row = TaskDraft(
        id=9,
        user_id=1,
        space_id=7,
        version=1,
        fields={"submitterType": "USER", "submissionSchema": []},
    )

    assert "新题目，尚未填写" in drafts.agent_view(row, SPACE, private=True)
    assert "第一道题写进它" in drafts.scene(row)

    row.fields = {**row.fields, "name": "课题 A"}
    assert "尚未填写" not in drafts.agent_view(row, SPACE, private=True)


def test_the_edit_page_changes_the_task_it_is_open_on():
    row = TaskDraft(id=9, user_id=1, space_id=7, task_id=88, version=1, fields={})

    told = drafts.scene(row, "Lab 3 缓存模拟器")

    assert "修改题目页" in told and "「Lab 3 缓存模拟器」" in told
    assert "不新建草稿" in told


def test_cheese_says_a_task_started_directly_locks_its_team_when_it_starts():
    """No application on a task started directly: the team locks when it
    starts, and that is what 芝士 reads back."""
    row = TaskDraft(
        id=6,
        user_id=1,
        space_id=7,
        version=1,
        fields={
            "name": "eBPF 延迟分析",
            "submitterType": "TEAM",
            "startMode": "DIRECT",
            "teamLockingPolicy": "LOCK_ON_APPROVAL",
        },
    )

    view = drafts.agent_view(row, SPACE, private=True)

    assert "team_locking: 开始做这道题后锁定" in view
    assert "申请通过" not in view and "报名通过" not in view
