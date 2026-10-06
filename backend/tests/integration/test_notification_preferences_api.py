"""GET/PUT /notifications/preferences —— 通知设置页读写的两个接口。

读取永远成功（没保存过就是设计稿默认），写入是整份替换。这一层只证明「接口按
契约收发、并且真的落到了那个人的行上」，渠道怎么裁由 `test_notification_preferences`
与投递那边去证明。
"""

from tests.conftest import seed_user

PATH = "/notifications/preferences"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get(client, token: str) -> dict:
    r = client.get(PATH, headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_reading_before_any_save_returns_the_mock_defaults(client):
    token = seed_user(client, "prefs-fresh")
    data = _get(client, token)
    assert data["inAppEnabled"] is True
    assert data["pushEnabled"] is True
    assert data["emailMode"] == "digest"
    assert data["quietHoursEnabled"] is True
    assert data["quietHoursStart"] == "22:00"
    assert data["quietHoursEnd"] == "08:00"
    assert data["digestCadence"] == "weekly"
    # 矩阵八行都在，且矩阵默认照抄设计稿。
    assert data["events"]["mention"] == {"inApp": True, "push": True, "email": True}
    assert data["events"]["reaction"] == {
        "inApp": True,
        "push": False,
        "email": False,
    }
    assert data["events"]["deviceInUse"] == {
        "inApp": True,
        "push": True,
        "email": False,
    }


def test_a_save_round_trips_and_persists(client):
    token = seed_user(client, "prefs-roundtrip")
    body = {
        "inAppEnabled": True,
        "pushEnabled": False,
        "emailMode": "instant",
        "quietHoursEnabled": False,
        "quietHoursStart": "23:30",
        "quietHoursEnd": "07:00",
        "digestCadence": "daily",
        "events": {
            "reaction": {"inApp": True, "push": True, "email": True},
            "deviceInUse": {"inApp": False, "push": False, "email": False},
        },
    }
    r = client.put(PATH, json=body, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["pushEnabled"] is False

    # 换一次请求重新读：写的是行，不是某个请求里的内存。
    saved = _get(client, token)
    assert saved["pushEnabled"] is False
    assert saved["emailMode"] == "instant"
    assert saved["quietHoursEnabled"] is False
    assert saved["quietHoursStart"] == "23:30"
    assert saved["digestCadence"] == "daily"
    assert saved["events"]["reaction"] == {
        "inApp": True,
        "push": True,
        "email": True,
    }
    assert saved["events"]["deviceInUse"] == {
        "inApp": False,
        "push": False,
        "email": False,
    }
    # 没在 `events` 里提到的行仍按设计稿默认，不被清掉。
    assert saved["events"]["mention"] == {"inApp": True, "push": True, "email": True}


def test_two_people_keep_separate_preferences(client):
    mine = seed_user(client, "prefs-mine")
    theirs = seed_user(client, "prefs-theirs")
    client.put(PATH, json={"pushEnabled": False}, headers=_auth(mine))
    assert _get(client, mine)["pushEnabled"] is False
    # 另一个人的行没有被带着改。
    assert _get(client, theirs)["pushEnabled"] is True


def test_a_malformed_quiet_hour_is_rejected(client):
    token = seed_user(client, "prefs-bad-hour")
    r = client.put(
        PATH,
        json={"quietHoursStart": "25:00"},
        headers=_auth(token),
    )
    # 形状不对的时间在 pydantic 那一层就被挡下，落成平台惯用的 400。
    assert r.status_code == 400, r.text
    assert r.json()["error"]["data"]["details"][0]["loc"] == [
        "body",
        "quietHoursStart",
    ]


def test_an_anonymous_caller_is_refused(client):
    r = client.get(PATH)
    assert r.status_code in (401, 403), r.text
