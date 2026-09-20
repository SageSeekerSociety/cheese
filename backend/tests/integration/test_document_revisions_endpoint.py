"""接受或拒绝一处修订时，写回的是读清单时那一版文件。

房间每一轮都可能重新交付同一个产出，写的是同一个路径。人在预览面板上按「接受」和芝士
重写这个文件之间只差几秒：没有这道检查，人的这一下把芝士刚交付的那份整个盖掉，两边都
不报错，谁也看不出丢了什么。
"""

import io
import uuid
import zipfile

import pytest

from app.domain.workspace import service as ws
from tests.delivery import delivery_task

pytest.importorskip("lxml", reason="修订解析要用 lxml")

DECL = "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
OPENXML = "http://schemas.openxmlformats.org"
WHEN = 'w:author="芝士" w:date="2026-09-18T02:00:00Z"'

DOCUMENT = f"""{DECL}<w:document {W}><w:body>
<w:p><w:r><w:t xml:space="preserve">合同期限为 </w:t></w:r>
<w:del w:id="1" {WHEN}><w:r><w:delText>30</w:delText></w:r></w:del>
<w:ins w:id="2" {WHEN}><w:r><w:t>60</w:t></w:r></w:ins>
<w:r><w:t xml:space="preserve"> 天。</w:t></w:r></w:p>
</w:body></w:document>"""

LATER = DOCUMENT.replace("合同期限为", "履约期限为")

CONTENT_TYPES = (
    f'{DECL}<Types xmlns="{OPENXML}/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.'
    'openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.'
    'wordprocessingml.document.main+xml"/>'
    "</Types>"
)

RELS = (
    f'{DECL}<Relationships xmlns="{OPENXML}/package/2006/relationships">'
    '<Relationship Id="rId1"'
    f' Type="{OPENXML}/officeDocument/2006/relationships/officeDocument"'
    ' Target="word/document.xml"/>'
    "</Relationships>"
)

PATH = "output/合同.docx"


def _docx(body: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("_rels/.rels", RELS)
        package.writestr("word/document.xml", body)
    return buffer.getvalue()


def _document_xml(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        return package.read("word/document.xml").decode()


@pytest.fixture
def contract(client) -> tuple[uuid.UUID, uuid.UUID]:
    project = client.post("/projects", json={"name": "P", "owner_handle": "alice"})
    pid = uuid.UUID(project.json()["data"]["id"])
    topic = client.post("/topics", json={"project_id": str(pid), "title": "合同"})
    tid = uuid.UUID(topic.json()["data"]["id"])
    ws.write_room_file(pid, tid, PATH, _docx(DOCUMENT))
    return pid, tid


def _listing(client, tid: uuid.UUID, task: str | None = None) -> dict:
    params = {"path": PATH, **({"task": task} if task else {})}
    response = client.get(f"/topics/{tid}/documents/revisions", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _decide(client, tid: uuid.UUID, **body):
    return client.post(
        f"/topics/{tid}/documents/revisions", json={"path": PATH, **body}
    )


def test_a_decision_that_lost_the_race_answers_409_and_changes_nothing(
    client, contract
):
    pid, tid = contract
    read = _listing(client, tid)
    assert [row["number"] for row in read["revisions"]] == [1]

    # 芝士 delivers the file again while the reader is looking at the list.
    ws.write_room_file(pid, tid, PATH, _docx(LATER))

    response = _decide(client, tid, version=read["version"], accept=[1])

    assert response.status_code == 409
    assert "履约期限为" in _document_xml(ws.read_room_file(pid, tid, PATH))


def test_a_decision_carrying_the_current_version_goes_through(client, contract):
    pid, tid = contract
    read = _listing(client, tid)

    response = _decide(client, tid, version=read["version"], accept=[1])

    assert response.status_code == 200, response.text
    done = response.json()["data"]
    assert done["revisions"] == []
    written = _document_xml(ws.read_room_file(pid, tid, PATH))
    assert "<w:ins" not in written and "<w:del" not in written
    assert ">60<" in written and ">30<" not in written
    # 处理完文件变了，版本也跟着变——面板拿这一个接着处理下一条。
    assert done["version"] != read["version"]
    assert done["version"] == _listing(client, tid)["version"]


def test_a_decision_with_no_version_is_refused(client, contract):
    _pid, tid = contract

    assert _decide(client, tid, accept=[1]).status_code == 422


def test_a_document_on_a_card_branch_is_read_and_written_there(client, contract):
    """改动那一格看的是任务工作树上的那一份，不是房间交付的那一份。

    同一个路径在两个库里可以是两份不同的文件，所以「处理哪一份的修订」必须由调用方
    说出来——不说的那个版本，是把审阅时的一下点击写进另一个文件里。
    """
    pid, tid = contract
    task = delivery_task(client, tid)
    target = ws.topic_worktree(pid, task.id) / PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_docx(LATER))

    read = _listing(client, tid, str(task.id))
    assert [row["number"] for row in read["revisions"]] == [1]

    answer = _decide(
        client, tid, version=read["version"], accept=[1], task=str(task.id)
    )
    assert answer.status_code == 200, answer.text

    # 分支上那一份处理过了……
    assert "<w:ins" not in _document_xml(target.read_bytes())
    # ……而房间交付的那一份一个字没动。
    assert "<w:ins" in _document_xml(ws.read_room_file(pid, tid, PATH))


def test_another_room_s_card_is_not_a_source(client, contract):
    _pid, tid = contract
    other = client.post("/projects", json={"name": "P2", "owner_handle": "alice"})
    other_pid = other.json()["data"]["id"]
    room = client.post("/topics", json={"project_id": other_pid, "title": "别人的房间"})
    stranger = delivery_task(client, room.json()["data"]["id"])

    answer = client.get(
        f"/topics/{tid}/documents/revisions",
        params={"path": PATH, "task": str(stranger.id)},
    )
    assert answer.status_code == 404
