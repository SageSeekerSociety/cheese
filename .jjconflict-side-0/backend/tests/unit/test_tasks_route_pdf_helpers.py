import pytest

from app.core.errors import BadRequestError


def test_apply_pdf_task_options_keeps_draft_content_and_applies_form_options() -> None:
    from app.api.routes.tasks import _apply_pdf_task_options

    draft = {
        "name": "PDF 赛题",
        "intro": "PDF 简介",
        "description": "PDF 详情",
        "space": 7,
        "categoryId": 3,
        "rank": 1,
    }
    task_options = {
        "name": "表单名称占位",
        "intro": "",
        "description": "",
        "space": 7,
        "submitterType": "TEAM",
        "rank": 2,
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": 1780156799999,
        "categoryId": 9,
        "minTeamSize": 2,
        "maxTeamSize": 5,
    }

    result = _apply_pdf_task_options(draft=draft, task_options=task_options)

    assert result["name"] == "PDF 赛题"
    assert result["intro"] == "PDF 简介"
    assert result["description"] == "PDF 详情"
    assert result["rank"] == 2
    assert result["categoryId"] == 9
    assert result["minTeamSize"] == 2
    assert result["maxTeamSize"] == 5


def test_apply_pdf_task_options_falls_back_to_draft_space_and_category() -> None:
    from app.api.routes.tasks import _apply_pdf_task_options

    result = _apply_pdf_task_options(
        draft={
            "name": "PDF 赛题",
            "intro": "PDF 简介",
            "description": "PDF 详情",
            "space": 7,
            "categoryId": 3,
        },
        task_options={
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": 1780156799999,
        },
    )

    assert result["space"] == 7
    assert result["categoryId"] == 3


def test_apply_pdf_task_options_requires_content_fields() -> None:
    from app.api.routes.tasks import _apply_pdf_task_options

    with pytest.raises(BadRequestError, match="Draft missing required content fields"):
        _apply_pdf_task_options(
            draft={"name": "PDF 赛题", "intro": "", "description": "PDF 详情"},
            task_options={"space": 7},
        )
