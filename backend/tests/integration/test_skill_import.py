"""A person writes a skill on the project's page, and a project's manager imports
one made elsewhere: read first, nothing saved until it is added, and what is
added reaches the project's sessions as it was read."""

import base64
import io
import uuid
import zipfile

import pytest

from app.core import storage as storage_module
from app.core.config import settings
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for
from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)

OWNER = "skill-owner"
MEMBER = "skill-member"

PDF_SKILL = """---
name: pdf
description: 需要读取、合并、拆分或填写 PDF 时
---

# PDF 处理

## 读取文字

先用 pdfplumber 取文本，扫描件再走 OCR。
"""


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    # The storage backend is a module-level singleton fixed at first use.
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path / "files"))
    monkeypatch.setattr(storage_module, "_storage_backend", None)


def _project(client) -> str:
    return new_project(client, name=f"导入-{uuid.uuid4().hex[:6]}", owner=OWNER)["id"]


def _zip(files: dict[str, str | bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return out.getvalue()


def _shipped(project_id: str) -> dict[str, str]:
    return payload_for(
        uuid.UUID(project_id),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=False,
    )["skills"]


def _preview(client, project: str, filename: str, data: bytes, who: str = OWNER):
    return client.post(
        f"/projects/{project}/skills/import-preview",
        json={"filename": filename, "content": base64.b64encode(data).decode()},
        headers=session_auth_headers(who),
    )


def _skills(client, project: str) -> list[dict]:
    listed = client.get(
        f"/projects/{project}/skills", headers=session_auth_headers(OWNER)
    )
    return listed.json()["data"]["data"]


def test_a_person_writes_a_skill_without_a_room_and_it_ships(client):
    project = _project(client)
    added = client.post(
        f"/projects/{project}/skills",
        json={
            "name": "grading",
            "title": "批改作业",
            "description": "老师让批改一批学生作业时",
            "body": "## 步骤与规则\n\n先看格式再看内容",
        },
        headers=session_auth_headers(OWNER),
    )
    assert added.status_code == 200, added.text
    assert added.json()["data"]["state"] == "active"
    assert added.json()["data"]["origin"] == "person"
    assert "先看格式再看内容" in _shipped(project)["skills/grading/SKILL.md"]


def test_reading_a_zip_lays_the_skill_out_and_saves_nothing(client):
    project = _project(client)
    data = _zip(
        {
            "pdf/SKILL.md": PDF_SKILL,
            "pdf/scripts/fill_form.py": "print('fill')\n",
            "pdf/reference.md": "# 参考\n",
            "pdf/logo.png": b"\x89PNG\r\n",
        }
    )

    read = _preview(client, project, "pdf.zip", data)

    assert read.status_code == 200, read.text
    skill = read.json()["data"]
    assert skill["name"] == "pdf"
    assert skill["title"] == "PDF 处理"
    assert skill["description"] == "需要读取、合并、拆分或填写 PDF 时"
    assert "pdfplumber" in skill["body"] and "# PDF 处理" not in skill["body"]
    assert set(skill["files"]) == {"scripts/fill_form.py", "reference.md"}
    assert skill["skipped"] == ["logo.png"], "a file left out was not reported"
    assert skill["scripts"] == 1
    assert _skills(client, project) == []
    assert "skills/pdf/SKILL.md" not in _shipped(project)


def test_an_added_import_ships_its_files(client):
    project = _project(client)
    skill = _preview(
        client,
        project,
        "pdf.zip",
        _zip({"pdf/SKILL.md": PDF_SKILL, "pdf/scripts/merge.py": "print(1)\n"}),
    ).json()["data"]

    added = client.post(
        f"/projects/{project}/skills",
        json={
            key: skill[key] for key in ("name", "title", "description", "body", "files")
        }
        | {"imported": True},
        headers=session_auth_headers(OWNER),
    )

    assert added.status_code == 200, added.text
    assert added.json()["data"]["origin"] == "import"
    shipped = _shipped(project)
    assert "pdfplumber" in shipped["skills/pdf/SKILL.md"]
    assert shipped["skills/pdf/scripts/merge.py"] == "print(1)\n"


def test_a_plain_member_cannot_import(client):
    project = _project(client)
    join_project_team(client, project, MEMBER)

    read = _preview(client, project, "SKILL.md", PDF_SKILL.encode(), who=MEMBER)
    assert read.status_code == 403

    added = client.post(
        f"/projects/{project}/skills",
        json={
            "name": "pdf",
            "title": "PDF 处理",
            "description": "需要处理 PDF 时",
            "body": "先取文本",
            "imported": True,
        },
        headers=session_auth_headers(MEMBER),
    )
    assert added.status_code == 403
    assert _skills(client, project) == []

    # Writing one by hand stays open to everyone in the project.
    written = client.post(
        f"/projects/{project}/skills",
        json={
            "name": "pdf",
            "title": "PDF 处理",
            "description": "需要处理 PDF 时",
            "body": "先取文本",
        },
        headers=session_auth_headers(MEMBER),
    )
    assert written.status_code == 200, written.text


def test_a_zip_reaching_outside_its_folder_brings_nothing_from_outside(client):
    project = _project(client)
    data = _zip(
        {
            "skill/SKILL.md": PDF_SKILL,
            "skill/../../etc/passwd.txt": "root\n",
            "/abs/evil.sh": "rm -rf /\n",
            "other/notes.md": "不是这个技能的\n",
        }
    )

    skill = _preview(client, project, "skill.zip", data).json()["data"]

    assert skill["files"] == {}


def test_an_upload_without_a_skill_md_is_refused(client):
    project = _project(client)
    read = _preview(client, project, "x.zip", _zip({"notes/readme.md": "# hi\n"}))
    assert read.status_code == 422
    assert read.json()["error"]["i18n"]["key"] == "skillImportNoSkillMd"


def _write(client, project: str, name: str, files: dict[str, str]):
    return client.post(
        f"/projects/{project}/skills",
        json={
            "name": name,
            "title": name,
            "description": "用的时候",
            "body": "照着做",
            "files": files,
        },
        headers=session_auth_headers(OWNER),
    )


@pytest.mark.parametrize(
    "files",
    [
        {"notes.md": "a", "notes.md/more.md": "b"},
        {"..\\..\\evil.py": "x"},
        {"bad\x00name.md": "x"},
    ],
)
def test_a_path_no_machine_can_write_is_refused_at_save(client, files):
    project = _project(client)
    assert _write(client, project, "fine", {"ok.md": "ok"}).status_code == 200

    refused = _write(client, project, "broken", files)

    assert refused.status_code == 422, refused.text
    # The project's other skills still ship.
    assert _shipped(project)["skills/fine/ok.md"] == "ok"


def test_a_file_two_skills_share_is_stored_once(client, tmp_path):
    project = _project(client)
    shared = "同一份参考\n" * 100
    assert _write(client, project, "one", {"ref.md": shared}).status_code == 200
    assert _write(client, project, "two", {"docs/ref.md": shared}).status_code == 200

    stored = [p for p in (tmp_path / "files").rglob("*") if p.is_file()]
    assert len(stored) == 1
    shipped = _shipped(project)
    assert shipped["skills/one/ref.md"] == shipped["skills/two/docs/ref.md"] == shared


def test_files_are_read_on_the_skill_not_in_the_list(client):
    project = _project(client)
    skill = _write(client, project, "one", {"scripts/a.py": "print(1)\n"}).json()[
        "data"
    ]

    listed = _skills(client, project)[0]
    detail = client.get(
        f"/skills/{skill['id']}", headers=session_auth_headers(OWNER)
    ).json()["data"]

    assert "print(1)" not in str(listed)
    assert detail["contents"] == {"scripts/a.py": "print(1)\n"}
