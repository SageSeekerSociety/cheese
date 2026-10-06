"""功能数据页：目录、问芝士那一页的每个数、窗口、隐私，以及访问记录本身。

这一批要钉的五件事，每一件都是「算错了也看不出来」的那一类：

* **目录页不带数字**。它是一张目的地清单，不是一个仪表盘；一个数出现在卡片上，
  读的人就会以为「这个数在这张卡上」是这一页要回答的问题。
* **分母**。「用问芝士的人占多少」的分母是**登录访问者**，不是全部访问者 —— 问芝士
  要登录，拿总访问量当分母会让这个比例随着未登录读者变多而下跌，读起来像「用的人
  变少了」。
* **分位数与按天**。tokens 的 p90 与直方图必须来自同一批行；折线的长度恒等于窗口
  天数，缺的那天补 0（判据是窗口，不是「有数据的那些天」）。
* **窗口**。7 天看不见 40 天前的那一行，90 天看得见。
* **不显示提问人**。「答不上来的问题」按原文聚合，返回体里没有任何一处 user id。

访问记录那一半（`POST /docs/visit`）：一个访问者一天一行、登录按账号去重、
没有可用编号的信标被收下并丢掉、保留期与问芝士一起走。

写数据一律走 `client` 那个库（`client.test_factory`），理由写在
`test_admin_stats.py` 的文件头。
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.docs_site import visits
from app.domain.docs_site.assistant import purge_old_questions
from app.domain.docs_site.models import DocsQuestion, DocsVisit
from tests.integration.conftest import (
    docs_cookie,
    docs_sign_in,
    on_docs,
    session_auth_headers,
    sign_in,
)

ADMIN = "feature-stats-admin"
STRANGER = "feature-stats-stranger"
ASKER_ONE = "feature-stats-asker-one"
ASKER_TWO = "feature-stats-asker-two"


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _today() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _days_ago(days: int) -> datetime:
    """A moment inside the UTC day ``days`` back — same bucket, never midnight."""
    return _today() - timedelta(days=days) + timedelta(hours=1)


def _day_of(days: int) -> date:
    return (_today() - timedelta(days=days)).date()


def _seed_questions(client, *rows: dict) -> None:
    async def _seed() -> None:
        async with client.test_factory() as s:
            for spec in rows:
                s.add(DocsQuestion(**spec))
            await s.commit()

    asyncio.run(_seed())


def _seed_visits(client, *rows: dict) -> None:
    async def _seed() -> None:
        async with client.test_factory() as s:
            for spec in rows:
                s.add(DocsVisit(**spec))
            await s.commit()

    asyncio.run(_seed())


def _report(client, admin: str, **params) -> dict:
    r = client.get(
        "/admin/feature-stats/docs-assistant",
        params=params,
        headers=session_auth_headers(admin),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _user_ids(client, *handles: str) -> dict[str, int]:
    """Get-or-create the accounts ``handles`` name, returning their user ids.

    The report counts askers by ``docs_questions.user_id``, so a test that wants
    a known number of askers has to hold real ids.
    """
    from app.domain.user.repositories import UserRepository

    out: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            repo = UserRepository(s)
            for handle in handles:
                user = await repo.get_by_username(handle)
                if user is None:
                    user = await repo.create_user(
                        username=handle, email=f"{handle}@example.com"
                    )
                out[handle] = user.id
            await s.commit()

    asyncio.run(_seed())
    return out


def _question(
    user_id: int, *, days: int = 1, outcome: str = "answered", **extra
) -> dict:
    """One row in ``docs_questions`` for ``user_id``."""
    body = {
        "question": extra.pop("question", "怎么邀请成员"),
        "page": extra.pop("page", None),
        "outcome": outcome,
        "sources": [],
        "latency_ms": extra.pop("latency_ms", 1000),
        "created_at": _days_ago(days),
        "user_id": user_id,
    }
    body.update(extra)
    return body


# ---------- 目录 ----------


def test_the_catalogue_lists_features_and_carries_no_numbers(client, as_admin):
    """目录页只列名字和一句说明 —— 卡片上不许出现任何数字。"""
    body = client.get(
        "/admin/feature-stats", headers=session_auth_headers(as_admin)
    ).json()["data"]

    ids = [feature["id"] for feature in body["features"]]
    assert "docs-assistant" in ids
    for feature in body["features"]:
        assert set(feature) == {"id", "title", "summary"}
        assert not any(ch.isdigit() for ch in json.dumps(feature, ensure_ascii=False))


def test_a_stranger_cannot_read_the_catalogue_or_a_report(client, as_admin):
    """后台的门对每一块都是同一道：非管理员进不去。"""
    headers = session_auth_headers(STRANGER)
    assert client.get("/admin/feature-stats", headers=headers).status_code == 403
    assert (
        client.get("/admin/feature-stats/docs-assistant", headers=headers).status_code
        == 403
    )


def test_an_unknown_feature_is_a_404(client, as_admin):
    assert (
        client.get(
            "/admin/feature-stats/nope", headers=session_auth_headers(as_admin)
        ).status_code
        == 404
    )


def test_days_is_bounded(client, as_admin):
    headers = session_auth_headers(as_admin)
    assert (
        client.get(
            "/admin/feature-stats/docs-assistant", params={"days": 0}, headers=headers
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/admin/feature-stats/docs-assistant", params={"days": 91}, headers=headers
        ).status_code
        == 400
    )


# ---------- 问芝士那一页 ----------


def test_the_page_counts_visitors_askers_and_the_answer_rate(client, as_admin):
    """顶上那五个数：来的人、其中登录的、用问芝士的（占登录访问者）、提问次数、成功率。"""
    ids = _user_ids(client, ASKER_ONE, ASKER_TWO)
    asker_one = ids[ASKER_ONE]
    asker_two = ids[ASKER_TWO]
    _seed_visits(
        client,
        # 两个登录读者，各自来过两天（第二天的行不该被算成第二个人）。
        {"user_id": asker_one, "visitor_id": f"u:{asker_one}", "day": _day_of(1)},
        {"user_id": asker_one, "visitor_id": f"u:{asker_one}", "day": _day_of(0)},
        {"user_id": asker_two, "visitor_id": f"u:{asker_two}", "day": _day_of(1)},
        # 一个没登录的读者，同一个人两个浏览器 = 两个访问者。
        {"user_id": None, "visitor_id": "v:aaaabbbb", "day": _day_of(1)},
        {"user_id": None, "visitor_id": "v:ccccdddd", "day": _day_of(1)},
    )
    _seed_questions(
        client,
        _question(asker_one),
        _question(asker_one),
        _question(asker_two),
        _question(asker_two, outcome="no_match"),
        _question(asker_one, outcome="failed"),
    )

    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["visitors"] == {"value": 4, "logged_in": 2}
    assert numbers["askers"]["value"] == 2
    # 分母是登录访问者（2），不是全部访问者（4）。
    assert numbers["askers"]["share"] == pytest.approx(1.0)
    assert numbers["questions"]["value"] == 5
    assert numbers["questions"]["per_asker"] == pytest.approx(2.5)
    assert numbers["answer_rate"]["answered"] == 3
    assert numbers["answer_rate"]["total"] == 5
    assert numbers["answer_rate"]["value"] == pytest.approx(0.6)


def test_a_rate_with_no_denominator_is_null_not_zero(client, as_admin):
    """没有人来过：比例没有值，而不是 0。"""
    numbers = _report(client, as_admin, days=30)["numbers"]
    assert numbers["visitors"]["value"] == 0
    assert numbers["askers"]["share"] is None
    assert numbers["answer_rate"]["value"] is None
    assert numbers["questions"]["per_asker"] is None


def test_tokens_quantiles_and_histogram_agree_about_their_input(client, as_admin):
    """五个统计量与直方图来自同一批行：数少了任何一条，两边就对不上。"""
    asker = _user_ids(client, ASKER_ONE)[ASKER_ONE]
    totals = [1000, 2000, 3000, 4000, 5000]
    _seed_questions(
        client,
        *[
            _question(
                asker,
                prompt_tokens=total // 2,
                completion_tokens=total - total // 2,
            )
            for total in totals
        ],
    )

    body = _report(client, as_admin, days=30)
    tokens = body["tokens"]

    assert tokens["count"] == 5
    assert tokens["min"] == pytest.approx(1000.0)
    assert tokens["max"] == pytest.approx(5000.0)
    assert tokens["median"] == pytest.approx(3000.0)
    assert tokens["avg"] == pytest.approx(3000.0)
    # percentile_cont 的线性插值：0.9 * (5-1) = 3.6 -> 4000 + 0.6 * 1000。
    assert tokens["p90"] == pytest.approx(4600.0)
    assert sum(bin_["count"] for bin_ in tokens["histogram"]) == 5


def test_latency_quantiles_ignore_the_slow_tail_of_nothing(client, as_admin):
    """耗时（中位数 / 90% / 最慢）与回答结果占比同源。"""
    asker = _user_ids(client, ASKER_ONE)[ASKER_ONE]
    _seed_questions(
        client,
        *[
            _question(asker, latency_ms=ms, outcome=outcome)
            for ms, outcome in [
                (1000, "answered"),
                (2000, "answered"),
                (3000, "no_match"),
                (4000, "failed"),
                (9000, "answered"),
            ]
        ],
    )

    body = _report(client, as_admin, days=30)
    assert body["latency"]["median"] == pytest.approx(3000.0)
    assert body["latency"]["max"] == pytest.approx(9000.0)
    assert body["outcomes"] == {
        "answered": 3,
        "no_match": 1,
        "failed": 1,
        "total": 5,
    }


def test_the_trend_is_dense_over_the_window_and_starts_at_the_oldest_day(
    client, as_admin
):
    """折线恒有 `days` 个点、最早在前、缺的那天是 0（判据是窗口，不是有数据的天）。"""
    asker = _user_ids(client, ASKER_ONE)[ASKER_ONE]
    _seed_visits(
        client,
        {"user_id": None, "visitor_id": "v:aaaabbbb", "day": _day_of(2)},
    )
    _seed_questions(client, _question(asker, days=2), _question(asker, days=0))

    trend = _report(client, as_admin, days=7)["trend"]

    assert len(trend) == 7
    assert trend[0]["date"] == _day_of(6).isoformat()
    assert trend[-1]["date"] == _day_of(0).isoformat()
    by_day = {row["date"]: row for row in trend}
    assert by_day[_day_of(2).isoformat()] == {
        "date": _day_of(2).isoformat(),
        "visitors": 1,
        "askers": 1,
        "questions": 1,
    }
    # 中间那天谁也没来、谁也没问 —— 是 0，不是缺行。
    assert by_day[_day_of(1).isoformat()]["visitors"] == 0
    assert by_day[_day_of(1).isoformat()]["questions"] == 0


def test_the_window_bounds_what_is_counted(client, as_admin):
    """7 天看不见 40 天前的行，90 天看得见。"""
    asker = _user_ids(client, ASKER_ONE)[ASKER_ONE]
    _seed_questions(client, _question(asker, days=40), _question(asker, days=1))

    assert _report(client, as_admin, days=7)["numbers"]["questions"]["value"] == 1
    assert _report(client, as_admin, days=90)["numbers"]["questions"]["value"] == 2
    assert _report(client, as_admin, days=90)["start"] == _day_of(89).isoformat()


def test_unanswered_questions_group_by_text_show_the_page_and_hide_the_asker(
    client, as_admin
):
    """答不上来的问题：按原文合并次数、附上提问时所在页面、不显示提问人。"""
    ids = _user_ids(client, ASKER_ONE, ASKER_TWO)
    one, two = ids[ASKER_ONE], ids[ASKER_TWO]
    _seed_questions(
        client,
        _question(one, outcome="no_match", question="能导出 PDF 吗", page="files"),
        _question(two, outcome="no_match", question="能导出 PDF 吗", page="files"),
        _question(one, outcome="no_match", question="能导出 PDF 吗", page="sites"),
        _question(two, outcome="no_match", question="能记住我说的话吗", page="teams"),
        # 答上来的不进这张表。
        _question(one, question="怎么邀请成员"),
    )

    body = _report(client, as_admin, days=30)
    rows = body["unanswered"]

    assert [row["question"] for row in rows] == ["能导出 PDF 吗", "能记住我说的话吗"]
    assert rows[0]["count"] == 3
    assert rows[0]["page"] == "files"
    assert rows[1]["count"] == 1
    # 隐私：这张表只有三列，整份返回体里也没有任何一处提问人。
    assert all(set(row) == {"question", "page", "count"} for row in rows)
    assert "user" not in json.dumps(body)


def test_the_cost_is_an_estimate_and_says_so(client, as_admin):
    """没有网关就没有金额 —— 页面上是破折号，不是 0。"""
    asker = _user_ids(client, ASKER_ONE)[ASKER_ONE]
    _seed_questions(client, _question(asker, prompt_tokens=100, completion_tokens=100))

    cost = _report(client, as_admin, days=30)["numbers"]["cost"]

    assert cost["usd"] is None
    assert cost["per_question"] is None
    assert cost["source"] == "unavailable"
    assert cost["unpriced_tokens"] == 200


# ---------- POST /docs/visit ----------


def _visit_rows(client) -> list[DocsVisit]:
    async def _read():
        async with client.test_factory() as s:
            return list(
                (
                    await s.execute(select(DocsVisit).order_by(DocsVisit.created_at))
                ).scalars()
            )

    return asyncio.run(_read())


def test_a_visitor_is_counted_once_a_day(client):
    """同一对 (访问者, 天) 只落一行 —— 信标可以重放，表不能跟着长。"""
    body = {"visitor": "aaaabbbbcccc", "page": "quickstart"}
    assert client.post("/docs/visit", json=body).status_code == 204
    assert client.post("/docs/visit", json=body).status_code == 204

    rows = _visit_rows(client)
    assert len(rows) == 1
    assert rows[0].visitor_id == "v:aaaabbbbcccc"
    assert rows[0].page == "quickstart"
    assert rows[0].day == _day_of(0)

    # 另一个浏览器编号是另一个访问者。
    client.post("/docs/visit", json={"visitor": "ddddeeeeffff"})
    assert len(_visit_rows(client)) == 2


def test_a_signed_in_visitor_is_counted_by_account_not_by_browser(client, docs_host):
    """同一个账号换一个浏览器来，还是同一个人。登录看的是文档站自己的 cookie。"""
    headers = on_docs(docs_cookie(docs_sign_in(client, sign_in(client, ASKER_ONE))))

    client.post("/docs/visit", json={"visitor": "aaaabbbbcccc"}, headers=headers)
    client.post("/docs/visit", json={"visitor": "zzzzyyyyxxxx"}, headers=headers)

    rows = _visit_rows(client)
    assert len(rows) == 1
    assert rows[0].user_id is not None
    assert rows[0].visitor_id.startswith("u:")


def test_a_beacon_without_a_usable_id_is_accepted_and_dropped(client):
    """收下、不记：信标那头没有人能看错误，编一个编号比留一个洞更糟。"""
    assert client.post("/docs/visit", json={"visitor": ""}).status_code == 204
    assert client.post("/docs/visit", json={"visitor": "短"}).status_code == 204
    assert _visit_rows(client) == []


def test_a_page_slug_outside_the_public_shape_is_dropped_not_stored(client):
    assert (
        client.post(
            "/docs/visit",
            json={"visitor": "aaaabbbbcccc", "page": "../../etc/passwd"},
        ).status_code
        == 204
    )
    assert _visit_rows(client)[0].page is None


def test_the_visit_limiter_stops_a_flood_without_refusing_it():
    """计数器不是门：超了就不记，但请求仍然是 204（页面上没有人能看错误）。"""

    class FakeRedis:
        def __init__(self) -> None:
            self.count = 0

        async def eval(self, *_args, **_kwargs) -> int:
            self.count += 1
            return self.count

    redis = FakeRedis()

    async def _run() -> tuple[bool, bool]:
        limits = visits.VisitLimits(lambda: redis)
        first = await limits.admit("v:aaaabbbbcccc")
        for _ in range(visits._PER_HOUR * 2):
            last = await limits.admit("v:aaaabbbbcccc")
        return first, last

    first, last = asyncio.run(_run())
    assert first is True
    # 第 1 次之后又打了 2 * _PER_HOUR 次，最后这次一定在限额之外。
    assert last is False


def test_the_limiter_allows_when_the_counter_is_unreachable():
    """Valkey 挂了就放行：丢掉的是几行访问记录，拒掉的是整段时间的所有访问。"""

    async def _run() -> bool:
        limits = visits.VisitLimits(lambda: None)
        return await limits.admit("v:aaaabbbbcccc")

    assert asyncio.run(_run()) is True


def test_visits_age_out_with_the_questions(client):
    """保留期和问芝士一致，而且走同一个清扫任务。"""
    old_day = (
        _today() - timedelta(days=settings.docs_question_retention_days + 1)
    ).date()
    _seed_visits(
        client,
        {"user_id": None, "visitor_id": "v:aaaabbbb", "day": old_day},
        {"user_id": None, "visitor_id": "v:ccccdddd", "day": _day_of(1)},
    )

    gone = asyncio.run(
        purge_old_questions(client.test_request_factory),
    )

    assert gone == 1
    remaining = _visit_rows(client)
    assert [row.visitor_id for row in remaining] == ["v:ccccdddd"]
