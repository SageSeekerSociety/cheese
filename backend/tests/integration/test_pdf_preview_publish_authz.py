"""PDF 预览那条路由：谁能让模型替这块板干活、谁能读回这块板的模板。

`POST /tasks/publish/from-pdf/preview`（`routes/tasks.py`）从前只
`require_auth_user`：`spaceId` 是 Form 字段、只判 `space is None`，随后
`template = draft_service.pick_template(space.task_templates or [], template_index)`
并把 `templateUsed` 放回返回体。实测（本文件第一条用例就是那次实测的沉淀）：

    outsider user_id=10（从没进过 space 4）
    POST /tasks/publish/from-pdf/preview {"spaceId": 4} → 200
    body.data.templateUsed = {"name": "BOARD-ONLY-TEMPLATE-MARKER-name", ...}
    日志：PDF import completed: user_id=10 pages=1 drafts=1 tokens=11

也就是说，任何人只要知道 spaceId（小整数、可枚举）就能：把模型的 token 花在别人的
板上，并把这块板的 `task_templates` 原样读回去 —— 而 `task_templates` 在
`GET /spaces/{spaceId}` 上是要先是这块板的人（`_ensure_space_visible`）才看得到的。

预览是发题的前半截：`confirm` 那条路逐条落到 `_create_task_entity`，发题的门就在
那里（`may_publish_in_space` —— 「这个板里的人都能发」）。这里照抄同一条判据与同一
句措辞，一处口径两处生效；不另立一套说法。

1. 无关的登录用户 → **403**，返回体里没有该板模板的任何字符，且**模型没被调用**
   （`llm.calls == []`：挡在解析 PDF 之前，不然挡的是响应、花的是 token）。
2. 板里的人（非管理员）→ 200，`templateUsed` 照旧是该板的模板 —— 这条门收的是
   「板外的人」，不是「模板本身」：`POST /tasks` 本来就允许板里任何人发题，
   预览不该比发题更紧。
3. 匿名 → 401（`require_auth_user`）。
"""

from __future__ import annotations

import json

import fitz
import pytest
from fastapi.testclient import TestClient

from app.api.routes.tasks import get_task_pdf_draft_service
from app.core.config import settings
from app.domain.feature_stats import pricing
from app.domain.gateway_chat import Completion, Usage
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService
from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)

# 只在板里的模板上出现的三个字符串：返回体里出现任何一个，就是模板被读走了。
MARKER = "BOARD-ONLY-TEMPLATE-MARKER"


class _FakeLLM:
    """假的模型客户端 —— 测试里不可能真调模型，但调用次数是真的要数的。"""

    model = "fake-draft-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> Completion:
        self.calls.append(kwargs)
        return Completion(
            json.dumps({"name": "AI 题", "intro": "简述", "description": "详细说明"}),
            Usage(prompt_tokens=5, completion_tokens=6),
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user: CreatedUser) -> str:
    return user_client.login(api_client, user.username, user.password)


def _pdf_bytes(text: str) -> bytes:
    """一页有字的 PDF —— 走完 `pymupdf4llm` 那一截要的是真文件。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块已批准的板，模板里带着只该这块板看得见的标记。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"PDF Preview Authz ({suffix})",
            "intro": "一块板",
            "description": "模板",
            "avatarId": 1,
            "taskTemplates": [
                {
                    "name": f"{MARKER}-name",
                    "intro": f"{MARKER}-intro",
                    "description": f"{MARKER}-description",
                }
            ],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "creator": creator,
        "creator_token": creator_token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


@pytest.fixture
def fake_llm(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> _FakeLLM:
    """把模型的调用换成假的：路由其余部分（鉴权、库、模板行、个人额度）全真。"""
    llm = _FakeLLM()
    service = TaskPdfDraftService(chat=llm)  # type: ignore[arg-type]
    api_client.app.dependency_overrides[get_task_pdf_draft_service] = lambda: service
    # 这个模型在网关上的单价：预览从发起人的个人额度里扣，没有单价就扣不了。
    monkeypatch.setattr(settings, "llm_gateway_credit_usd", 0.01)

    async def rates(*_args, **_kwargs):
        return {_FakeLLM.model: (1e-3, 2e-3, 1e-4, 1e-3)}

    monkeypatch.setattr(pricing, "model_rates", rates)
    try:
        yield llm
    finally:
        api_client.app.dependency_overrides.pop(get_task_pdf_draft_service, None)


def _outsider(
    user_client: UserCreator, api_client: TestClient
) -> tuple[CreatedUser, str]:
    """从没进过这块板的人 —— 登录了，别的什么都没有。"""
    user = user_client.create_user()
    return user, _login(user_client, api_client, user)


def _member_of(
    user_client: UserCreator, api_client: TestClient, board: dict
) -> tuple[CreatedUser, str]:
    """板上的人 —— 看得见这块板，能发题，但不是管理员。"""
    user = user_client.create_user()
    token = _login(user_client, api_client, user)
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return user, token


def _preview(
    api_client: TestClient,
    space_id: int,
    token: str | None,
    *,
    text: str = "赛题说明。",
):
    return api_client.post(
        "/tasks/publish/from-pdf/preview",
        data={"spaceId": str(space_id)},
        files={"file": ("preview.pdf", _pdf_bytes(text), "application/pdf")},
        headers=_auth(token) if token else {},
    )


def test_an_unrelated_user_cannot_preview_with_another_boards_template(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    fake_llm: _FakeLLM,
) -> None:
    """板外的人：403，一个模板字符都拿不到，模型也没被叫起来。"""
    outsider, outsider_token = _outsider(user_client, api_client)
    assert outsider.user_id not in (board["creator"].user_id,)

    resp = _preview(api_client, board["space_id"], outsider_token)

    assert resp.status_code == 403, resp.text
    assert MARKER not in resp.text, resp.text
    assert fake_llm.calls == [], "挡在调用之前：token 不该为板外的人花"


def test_an_anonymous_caller_cannot_preview(
    api_client: TestClient,
    board: dict,
    fake_llm: _FakeLLM,
) -> None:
    resp = _preview(api_client, board["space_id"], None)
    assert resp.status_code == 401, resp.text
    assert MARKER not in resp.text, resp.text
    assert fake_llm.calls == []


def test_a_member_of_the_board_still_previews_with_this_boards_template(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    fake_llm: _FakeLLM,
) -> None:
    """板里的人：预览照旧，`templateUsed` 还是要能看到这块板自己的模板。

    这道门收的是「板外的人」，不是「模板」—— `POST /tasks` 允许板里任何人发题
    （`may_publish_in_space` 问的就是 `is_member`），预览不该比发题更紧。
    """
    _member, member_token = _member_of(user_client, api_client, board)

    resp = _preview(api_client, board["space_id"], member_token)

    assert resp.status_code == 200, resp.text
    template_used = resp.json()["data"]["templateUsed"]
    assert template_used["name"] == f"{MARKER}-name"
    assert len(fake_llm.calls) == 1


def test_a_space_that_does_not_exist_is_still_not_found(
    api_client: TestClient,
    user_client: UserCreator,
    fake_llm: _FakeLLM,
) -> None:
    """既有行为没被碰松：不存在的板还是 404，不是 403。"""
    _outsider_user, outsider_token = _outsider(user_client, api_client)
    resp = _preview(api_client, 999_999_999, outsider_token)
    assert resp.status_code == 404, resp.text
    assert fake_llm.calls == []


def test_a_preview_is_paid_for_by_the_person_who_asked_for_it(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    fake_llm: _FakeLLM,
    db_session,
) -> None:
    """发起预览的人付钱：一次预览扣一次他的个人额度，按这个模型的单价算。"""
    member, member_token = _member_of(user_client, api_client, board)

    assert _preview(api_client, board["space_id"], member_token).status_code == 200

    [grant] = _personal_grants(api_client, db_session, member.user_id)
    cost = 5 * 1e-3 + 6 * 2e-3
    assert grant["credits_used"] == pytest.approx(cost / 0.01)


def test_with_no_credits_left_the_model_is_not_asked(
    api_client: TestClient,
    user_client: UserCreator,
    board: dict,
    fake_llm: _FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "personal_credits_monthly", 1.0)
    member, member_token = _member_of(user_client, api_client, board)
    # 第一次把这个月的额度用超，第二次在调模型之前就被拦下。
    assert _preview(api_client, board["space_id"], member_token).status_code == 200
    calls = len(fake_llm.calls)

    resp = _preview(api_client, board["space_id"], member_token)
    assert resp.status_code == 429, resp.text
    assert "额度已用完" in resp.json()["message"]
    assert len(fake_llm.calls) == calls


def _personal_grants(api_client: TestClient, session, user_id: int) -> list[dict]:
    from sqlalchemy import select

    from app.domain.usage.models import ComputeGrant

    async def read() -> list[dict]:
        rows = (
            await session.execute(
                select(ComputeGrant)
                .where(ComputeGrant.user_id == user_id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [{"credits_used": g.credits_used} for g in rows]

    return api_client.portal.call(read)  # type: ignore[union-attr]
