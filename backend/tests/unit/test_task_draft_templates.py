"""芝士 fills a draft from a space template, as 「使用模板」 does on the page."""

import json
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import BadRequestError
from app.domain.task import drafts

SKELETON = json.dumps(
    {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "背景"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "写清楚要解决的问题。"}],
            },
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "交付"}],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "源码 "},
                                    {
                                        "type": "text",
                                        "text": "main.c",
                                        "marks": [{"type": "code"}],
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
    }
)

SPACE = drafts.Space(
    id=7,
    name="计算机系统基础",
    categories={"实验": 1},
    tags={},
    zone=ZoneInfo("Asia/Shanghai"),
    templates=(
        {
            "name": "标准实验",
            "description": "一周的实验，交报告与代码",
            "title": "Lab N",
            "content": SKELETON,
            "submitterType": "USER",
            "rank": 2,
            "defaultDeadline": 7,
        },
    ),
)


def test_a_template_fills_the_form_and_later_fields_win():
    fields = drafts.from_agent(
        {"template": "标准实验", "name": "Lab 3 缓存模拟器"}, SPACE, {}
    )

    assert fields["name"] == "Lab 3 缓存模拟器"
    assert (
        fields["descriptionMarkdown"]
        == "### 背景\n\n写清楚要解决的问题。\n\n### 交付\n\n- 源码 `main.c`"
    )
    assert (
        fields["rank"] == 2
        and fields["defaultDeadline"] == 7
        and fields["submitterType"] == "USER"
    )


def test_a_template_the_space_lacks_is_refused_with_the_ones_it_has():
    with pytest.raises(BadRequestError) as caught:
        drafts.from_agent({"template": "随堂小测"}, SPACE, {})
    assert "标准实验" in repr(caught.value.args)


def test_the_draft_names_the_space_templates():
    row = drafts.TaskDraft(id=1, user_id=1, space_id=7, fields={}, marks={}, version=0)
    view = drafts.agent_view(row, SPACE, private=True)
    assert "这个空间的模板：标准实验（一周的实验，交报告与代码）" in view
