"""从标准模板新建一份房间文件：拿到的是模板的副本，之后就是一份普通的房间文件。"""

from app.domain.documents import catalogue
from tests.integration.conftest import post_project


def _room(client) -> str:
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    return client.post(
        "/topics", json={"project_id": project, "title": "写周报"}
    ).json()["data"]["id"]


def test_every_listed_template_makes_a_file_of_its_format(client):
    room = _room(client)
    listed = client.get(f"/topics/{room}/files/templates").json()["data"]["data"]
    assert {t["id"] for t in listed} == {t.id for t in catalogue.TEMPLATES}
    for template in listed:
        path = f"文档/{template['name']}.{template['suffix']}"
        made = client.post(
            f"/topics/{room}/files/new", json={"template": template["id"], "path": path}
        )
        assert made.status_code == 200, made.text
        raw = client.get(f"/topics/{room}/files/raw", params={"path": path})
        assert raw.content == catalogue.template_bytes(catalogue.find(template["id"]))
        history = client.get(
            f"/topics/{room}/files/revisions", params={"path": path}
        ).json()["data"]["data"]
        assert [h["source"] for h in history] == ["template"]


def test_a_new_document_does_not_replace_an_existing_one(client):
    room = _room(client)
    body = {"template": "weekly", "path": "周报.docx"}
    assert client.post(f"/topics/{room}/files/new", json=body).status_code == 200
    again = client.post(f"/topics/{room}/files/new", json=body)
    assert again.status_code == 409


def test_a_template_keeps_its_format(client):
    room = _room(client)
    wrong = client.post(
        f"/topics/{room}/files/new", json={"template": "analysis", "path": "表.docx"}
    )
    assert wrong.status_code == 422
    assert "xlsx" in wrong.text


def test_a_room_file_can_be_shown_in_the_room_after_creation(client):
    room = _room(client)
    client.post(
        f"/topics/{room}/files/new", json={"template": "report", "path": "报告.docx"}
    )
    shown = client.get(f"/topics/{room}/shown").json()["data"]["data"]
    assert "报告.docx" in [row["path"] for row in shown]
