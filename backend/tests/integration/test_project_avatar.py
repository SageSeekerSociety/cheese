"""A project's avatar, as decided before the code was written:

- a new project shows the default tile: no kind, no colour of its own;
- whoever may edit the project's settings sets it to an icon, an emoji, a few
  letters or an uploaded picture, with a colour, and everyone who lists the
  project sees what was set;
- text fits the tile: at most two CJK characters or four letters and digits;
- an emoji is one emoji, and a picture is one that was uploaded;
- a member who may not edit the settings cannot change it.
"""

import pytest

from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)

OWNER = session_auth_headers("owner")
MEMBER = session_auth_headers("member")


def _set(client, project: str, body: dict, headers=OWNER):
    return client.put(f"/projects/{project}/avatar", json=body, headers=headers)


def _listed(client, project: str) -> dict:
    rows = client.get("/projects", headers=OWNER).json()["data"]["data"]
    return next(p for p in rows if p["id"] == project)["avatar"]


def test_a_new_project_shows_the_default_tile(client):
    project = new_project(client)
    assert project["avatar"] == {
        "kind": None,
        "glyph": None,
        "hue": None,
        "image_id": None,
    }


def test_an_avatar_set_is_what_the_project_list_shows(client):
    project = new_project(client)["id"]
    saved = _set(
        client, project, {"kind": "icon", "glyph": "flask-outline", "hue": 200}
    )
    assert saved.status_code == 200, saved.text
    expected = {"kind": "icon", "glyph": "flask-outline", "hue": 200, "image_id": None}
    assert saved.json()["data"] == expected
    assert _listed(client, project) == expected
    one = client.get(f"/projects/{project}", headers=OWNER).json()["data"]
    assert one["avatar"] == expected

    # Back to the default letters, keeping the colour.
    assert _set(client, project, {"hue": 200}).status_code == 200
    assert _listed(client, project) == {
        "kind": None,
        "glyph": None,
        "hue": 200,
        "image_id": None,
    }


@pytest.mark.parametrize("text", ["算法", "CS", "AB12", "数A", "x"])
def test_text_that_fits_the_tile_is_kept(client, text):
    project = new_project(client)["id"]
    saved = _set(client, project, {"kind": "text", "glyph": text})
    assert saved.status_code == 200, saved.text
    assert _listed(client, project)["glyph"] == text


@pytest.mark.parametrize("text", ["数据结构", "ABCDE", "数AB1", "A-B", "", "  "])
def test_text_that_does_not_fit_is_refused(client, text):
    project = new_project(client)["id"]
    refused = _set(client, project, {"kind": "text", "glyph": text})
    assert refused.status_code == 422, refused.text
    assert _listed(client, project)["kind"] is None


@pytest.mark.parametrize("emoji", ["🚀", "👍🏽", "🇨🇳", "👨‍👩‍👧"])
def test_one_emoji_is_kept(client, emoji):
    project = new_project(client)["id"]
    assert _set(client, project, {"kind": "emoji", "glyph": emoji}).status_code == 200
    assert _listed(client, project)["glyph"] == emoji


@pytest.mark.parametrize("emoji", ["🚀🚀", "A", "中"])
def test_anything_but_one_emoji_is_refused(client, emoji):
    project = new_project(client)["id"]
    assert _set(client, project, {"kind": "emoji", "glyph": emoji}).status_code == 422


def test_a_picture_nobody_uploaded_is_refused(client):
    project = new_project(client)["id"]
    refused = _set(client, project, {"kind": "image", "image_id": 987654})
    assert refused.status_code == 422, refused.text


def test_a_member_who_may_not_edit_settings_cannot_change_it(client):
    project = new_project(client)["id"]
    join_project_team(client, project, "member")
    refused = _set(client, project, {"kind": "emoji", "glyph": "🚀"}, headers=MEMBER)
    assert refused.status_code == 403, refused.text
    assert _listed(client, project)["kind"] is None
