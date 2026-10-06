"""Each new report is announced once in the team's Feishu group — if one is set.

Pinned: with no group configured nothing goes out; a public report's message
carries its number, title, author and a link; a private report's message carries
its number and a link and none of what its author chose not to show everyone.
"""

import pytest

from app.core import alerting
from app.core.config import settings
from tests.integration.conftest import session_auth_headers

HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test"
REPORTER = "fba-reporter"


@pytest.fixture
def posted(monkeypatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(alerting, "post", lambda url, text: sent.append((url, text)))
    return sent


def _report(client, **body) -> dict:
    r = client.post(
        "/feedback",
        json={"title": "导出按钮没反应", **body},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_no_group_configured_sends_nothing(client, posted, monkeypatch):
    monkeypatch.setattr(settings, "feishu_feedback_webhook", "")

    _report(client)

    assert posted == []


def test_a_public_report_is_announced_once_with_what_anyone_could_read(
    client, posted, monkeypatch
):
    monkeypatch.setattr(settings, "feishu_feedback_webhook", HOOK)

    row = _report(client, problem="点了导出，页面没有任何变化")

    assert len(posted) == 1
    url, text = posted[0]
    assert url == HOOK
    assert row["display_id"] in text
    assert "导出按钮没反应" in text
    assert REPORTER in text
    assert "点了导出，页面没有任何变化" in text
    assert f"/feedback/{row['id']}" in text


def test_a_private_report_is_announced_without_its_contents(
    client, posted, monkeypatch
):
    monkeypatch.setattr(settings, "feishu_feedback_webhook", HOOK)

    row = _report(client, visibility="private", problem="我的账号信息")

    assert len(posted) == 1
    _url, text = posted[0]
    assert row["display_id"] in text
    assert f"/feedback/{row['id']}" in text
    assert "导出按钮没反应" not in text
    assert "我的账号信息" not in text
    assert REPORTER not in text


def test_a_refused_report_is_not_announced(client, posted, monkeypatch):
    monkeypatch.setattr(settings, "feishu_feedback_webhook", HOOK)

    r = client.post(
        "/feedback", json={"title": ""}, headers=session_auth_headers(REPORTER)
    )

    assert r.status_code >= 400
    assert posted == []
