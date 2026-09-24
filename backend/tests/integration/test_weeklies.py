"""周报集 — POST /topics/{id}/weekly 与 GET /projects/{id}/weeklies.

这里钉住的是那个洞本身：周报集以前是 `listTopics(项目).filter(t =>
t.title.includes('周报'))` —— 一个人开个房间叫「周报怎么发」，它也会被列成一份
周报；而后端没有任何东西在产出周报，空态却写着「周报由芝士定期产出」。现在一份
周报是一条 kind=weekly 的项目级记录，谁的标题都不算数。
"""

from datetime import UTC, datetime, timedelta

from tests.integration.conftest import post_project


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str, title: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _weeklies(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/weeklies")
    assert r.status_code == 200
    return r.json()["data"]["data"]


def test_a_recorded_report_is_what_the_project_documents_page_lists(client):
    project = _make_project(client)
    room = _make_topic(client, project, "把产物页做出来")

    r = client.post(
        f"/topics/{room}/weekly",
        json={
            "body": "本周交付了产物页预览。",
            "since": "2026-08-31T00:00:00Z",
            "until": "2026-09-06T23:59:59Z",
        },
    )
    assert r.status_code == 200
    block = r.json()["data"]
    assert block["kind"] == "weekly"

    listed = _weeklies(client, project)
    assert [b["id"] for b in listed] == [block["id"]]
    assert listed[0]["content"] == "本周交付了产物页预览。"
    # 窗口是这一行的身份：并排摆着的几份周报，是它把它们分开的。
    assert listed[0]["meta"]["since"] == "2026-08-31T00:00:00+00:00"
    assert listed[0]["meta"]["until"] == "2026-09-06T23:59:59+00:00"
    # 而它写在哪，是那一行「来自话题」的落点。
    assert listed[0]["topic_id"] == room


def test_a_room_whose_title_says_weekly_is_not_a_report(client):
    project = _make_project(client)
    _make_topic(client, project, "周报怎么发")
    assert _weeklies(client, project) == []


def test_a_report_from_another_project_is_not_listed_here(client):
    mine = _make_project(client)
    theirs = _make_project(client)
    room = _make_topic(client, theirs, "房")
    posted = client.post(f"/topics/{room}/weekly", json={"body": "他们的"})
    assert posted.status_code == 200
    assert _weeklies(client, mine) == []
    assert len(_weeklies(client, theirs)) == 1


def test_the_window_defaults_to_the_week_ending_now(client):
    project = _make_project(client)
    room = _make_topic(client, project, "房")
    posted = client.post(f"/topics/{room}/weekly", json={"body": "这周没动静。"})
    assert posted.status_code == 200
    meta = _weeklies(client, project)[0]["meta"]
    since = datetime.fromisoformat(meta["since"])
    until = datetime.fromisoformat(meta["until"])
    assert until - since == timedelta(days=7)
    assert abs((datetime.now(UTC) - until).total_seconds()) < 60


def test_a_window_without_a_timezone_is_read_as_utc(client):
    """前端只给到日期是常事；那不该变成 naive/aware 相减时的抛错。"""
    project = _make_project(client)
    room = _make_topic(client, project, "房")
    r = client.post(
        f"/topics/{room}/weekly",
        json={"body": "x", "since": "2026-08-31", "until": "2026-09-06"},
    )
    assert r.status_code == 200
    assert _weeklies(client, project)[0]["meta"]["since"] == "2026-08-31T00:00:00+00:00"


def test_a_blank_report_and_a_backwards_window_are_both_refused(client):
    project = _make_project(client)
    room = _make_topic(client, project, "房")
    assert client.post(f"/topics/{room}/weekly", json={"body": "  "}).status_code == 422
    r = client.post(
        f"/topics/{room}/weekly",
        json={"body": "x", "since": "2026-09-06", "until": "2026-08-31"},
    )
    assert r.status_code == 422
    # 两次都什么都没记下：被拒的那一次不该留下半份周报。
    assert _weeklies(client, project) == []
