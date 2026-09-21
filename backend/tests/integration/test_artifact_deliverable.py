"""这一版交出去的是什么 (#1085 结论五)。

一版是一次交付，所以交付物记在那张卡上，而字节只在递卡那一刻读得到 —— 构建产物活
在那一轮的工作目录里，过后就没了。这里问的都是这件事的后果：交出去的那一份以后还
拿不拿得到、拿到的是不是当时那一份、三种交法在页面上是不是三句不同的话。

版号也在这里验：它不存在任何一张表上，是按采纳时间数出来的，所以撤回一次采纳，它
后面那几版的号自己往前挪。
"""

import asyncio
import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent.harness import deployment_harness
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.agent_session.services import AgentSessionService
from app.domain.review import services as review_services
from app.domain.topic.models import Topic
from tests.delivery import delivery_artifact, delivery_headers, delivery_task
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import _give_card_a_pr, _rendered_head
from tests.integration.test_accept_pr import app_world as app_world
from tests.integration.test_project_artifacts import remote_delivery as remote_delivery


@pytest.fixture(autouse=True)
def task_machine(client, monkeypatch, tmp_path):
    executor = object.__new__(Executor)
    executor.env = {"HOME": str(tmp_path / "machine")}
    client.test_machine_home = tmp_path / "machine"

    async def call(target, method, params):
        assert target["kind"] == "device" and method == "task_fs"
        return executor.task_fs(params)

    monkeypatch.setattr(execution, "call", call)


def _pdf(raw: bytes = b"PK\x03\x04doc") -> object:
    """一个成功的渲染服务：收到什么就当成转换出来了。"""

    async def ask(_raw, _suffix, _target, _endpoint, _timeout):
        return b"%PDF-1.7 " + raw

    return ask


def _project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str = "做一个东西") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    room_id = r.json()["data"]["id"]

    async def place():
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(room_id))
            await AgentSessionService(session).remember_place(
                topic_id=room.id,
                agent_handle="cheese",
                work_lease={"kind": "device"},
                runtime_location={
                    "device_id": "test-device",
                    "channel": "central",
                    "resource_id": str(room.resource_id or room.id),
                },
                harness=deployment_harness(),
            )
            await session.commit()

    asyncio.run(place())
    return room_id


def _hand_over(client, room_id: str, *, files=None, again=False, **declared):
    """递一张卡，可选地先把交付物放进这条活的工作目录里。

    `again`：这个房间的又一次交付。采纳会关闭那条活，所以第二次交付走的是新开
    的一条 —— 和真实情况一样（一条活交付一次）。
    """
    task = delivery_task(client, room_id, new=again)
    if files:
        for name, content in files.items():
            path = client.test_machine_home / ".cheese/tasks" / str(task.id) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    body = {
        "change_subject": "docs(report): finalise the report",
        "reviewer_handle": "alice",
        **delivery_artifact(
            client,
            room_id,
            hands_over=bool(declared.get("deliver") or declared.get("deliver_url")),
        ),
        **declared,
    }
    response = client.post(
        f"/topics/{room_id}/tasks/{task.id}/accept-card",
        headers=delivery_headers(client, room_id),
        json=body,
    )
    if response.status_code == 200:
        world = client.artifact_forge
        head = _give_card_a_pr(
            client,
            world,
            room_id,
            response.json()["data"]["id"],
            number=100 + len(world["fake"].prs),
        )
        world["fake"].check_state_by_sha[head] = ("success", "All checks passed")
    return response


def _accept(client, card_id: str):
    r = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card_id)},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r


def _artifact_id(client, project_id: str, name: str = "报告") -> str:
    rows = client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"]
    return next(row["id"] for row in rows if row["name"] == name)


def _detail(client, project_id: str, artifact_id: str) -> dict:
    r = client.get(f"/projects/{project_id}/artifacts/{artifact_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_the_file_that_was_handed_over_can_still_be_taken_later(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(
        client,
        room_id,
        files={"out/结题报告.pdf": "第一版的正文\n"},
        deliver="out/结题报告.pdf",
    )
    assert filed.status_code == 200, filed.text
    _accept(client, filed.json()["data"]["id"])

    artifact_id = _artifact_id(client, project_id)
    detail = _detail(client, project_id, artifact_id)
    assert detail["version"] == 1
    (version,) = detail["versions"]
    assert version["number"] == 1
    assert version["kind"] == "file"
    # 文件名是它的身份的一部分：下载下来该还叫这个名字，而不是一串 id。
    assert version["filename"] == "结题报告.pdf"

    got = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}"
        f"/versions/{version['card_id']}/file"
    )
    assert got.status_code == 200, got.text
    assert got.content == "第一版的正文\n".encode()


def test_a_later_version_does_not_overwrite_the_one_before_it(client):
    """七版各是各的：交出去过的那一份不会被下一次交付改写。"""
    project_id = _project(client)
    room_id = _room(client, project_id)

    first = _hand_over(
        client, room_id, files={"out/报告.pdf": "第一版\n"}, deliver="out/报告.pdf"
    )
    _accept(client, first.json()["data"]["id"])
    second = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "第二版\n"},
        deliver="out/报告.pdf",
        again=True,
    )
    _accept(client, second.json()["data"]["id"])

    artifact_id = _artifact_id(client, project_id)
    detail = _detail(client, project_id, artifact_id)
    assert detail["version"] == 2
    assert [v["number"] for v in detail["versions"]] == [1, 2]

    bytes_by_version = {
        v["number"]: client.get(
            f"/projects/{project_id}/artifacts/{artifact_id}"
            f"/versions/{v['card_id']}/file"
        ).content
        for v in detail["versions"]
    }
    assert bytes_by_version == {1: "第一版\n".encode(), 2: "第二版\n".encode()}

    compared = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}/compare",
        params={
            "before": detail["versions"][0]["card_id"],
            "after": detail["versions"][1]["card_id"],
        },
    )
    assert compared.status_code == 200, compared.text
    result = compared.json()["data"]
    assert result["identical"] is False
    assert "-第一版\n" in result["files"][0]["diff"]
    assert "+第二版\n" in result["files"][0]["diff"]


def test_comparison_only_reads_accepted_versions_of_this_artifact(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    first = _hand_over(
        client, room_id, files={"report.txt": "original"}, deliver="report.txt"
    )
    first_id = first.json()["data"]["id"]
    _accept(client, first_id)
    second = _hand_over(
        client,
        room_id,
        files={"report.txt": "unaccepted"},
        deliver="report.txt",
        again=True,
    )
    artifact_id = _artifact_id(client, project_id)
    url = f"/projects/{project_id}/artifacts/{artifact_id}/compare"
    assert (
        client.get(
            url, params={"before": first_id, "after": second.json()["data"]["id"]}
        ).status_code
        == 404
    )
    assert (
        client.get(
            url, params={"before": first_id, "after": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    same = client.get(url, params={"before": first_id, "after": first_id})
    assert same.status_code == 200, same.text
    assert same.json()["data"]["identical"] is True
    assert same.json()["data"]["files"][0]["diff"] == ""
    other = _project(client)
    assert (
        client.get(
            f"/projects/{other}/artifacts/{artifact_id}/compare",
            params={"before": first_id, "after": first_id},
        ).status_code
        == 404
    )


def test_comparison_of_source_deliveries_uses_the_two_accepted_commits(
    client, monkeypatch
):
    from app.domain.repository.forge_files import ProjectFiles
    from app.domain.review.models import AcceptCard

    project_id = _project(client)
    room_id = _room(client, project_id)
    ids, revisions = [], []
    for again in (False, True):
        filed = _hand_over(client, room_id, again=again)
        card_id = filed.json()["data"]["id"]
        _accept(client, card_id)
        ids.append(card_id)

        async def revision(card_id=card_id):
            async with client.test_factory() as session:
                return (await session.get(AcceptCard, uuid.UUID(card_id))).pr_head_sha

        revisions.append(asyncio.run(revision()))

    async def compare(self, before, after):
        assert str(self.project_id) == project_id
        assert (before, after) == tuple(revisions)
        return [{"path": "report.py", "diff": "-old\n+new", "note": None}]

    monkeypatch.setattr(ProjectFiles, "compare_revisions", compare)
    # 两次交出去的都是合并，所以两版都落在项目那个仓库那一项上 —— 它跟项目同名。
    artifact_id = _artifact_id(client, project_id, "P")
    response = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}/compare",
        params={"before": ids[0], "after": ids[1]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["kind"] == "merge"
    assert response.json()["data"]["files"][0]["diff"] == "-old\n+new"


def test_a_version_that_lands_after_a_revoke_takes_the_number_that_freed_up(client):
    """版号是数出来的，所以撤回一次采纳，后面那几版自己往前挪。"""
    project_id = _project(client)
    room_id = _room(client, project_id)

    first = _hand_over(
        client, room_id, files={"out/报告.pdf": "第一版\n"}, deliver="out/报告.pdf"
    )
    first_id = first.json()["data"]["id"]
    _accept(client, first_id)
    second = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "第二版\n"},
        deliver="out/报告.pdf",
        again=True,
    )
    _accept(client, second.json()["data"]["id"])

    revoked = client.post(
        f"/accept-cards/{first_id}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert revoked.status_code == 200, revoked.text

    detail = _detail(client, project_id, _artifact_id(client, project_id))
    assert detail["version"] == 1
    (only,) = detail["versions"]
    assert only["number"] == 1
    # 留下的是第二次交付那一份，它现在就是第一版。
    assert (
        client.get(
            f"/projects/{project_id}/artifacts/{_artifact_id(client, project_id)}"
            f"/versions/{only['card_id']}/file"
        ).content
        == "第二版\n".encode()
    )


def test_an_address_is_recorded_as_a_pointer_and_has_no_file(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(client, room_id, deliver_url="https://example.com/site")
    assert filed.status_code == 200, filed.text
    _accept(client, filed.json()["data"]["id"])

    artifact_id = _artifact_id(client, project_id)
    (version,) = _detail(client, project_id, artifact_id)["versions"]
    assert version["kind"] == "link"
    assert version["url"] == "https://example.com/site"

    got = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}"
        f"/versions/{version['card_id']}/file"
    )
    # 没有文件不是「文件丢了」：交出去的是一个地址。
    assert got.status_code == 404

    compared = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}/compare",
        params={"before": version["card_id"], "after": version["card_id"]},
    ).json()["data"]
    assert compared["kind"] == "link"
    assert compared["identical"] is True
    assert compared["note"] == "link"


def test_office_preview_converts_the_selected_retained_file(client, monkeypatch):
    """产物页上「打开」要的那一份，是当时交出去的那一份的预览形态。

    这里替换的是渲染服务本身（`office._ask`），不是某一条路由里的转发函数：
    从请求到字节的那条路（转换表、大小闸门、结果校验、缓存）都要是真的走一遍。
    """
    from app.core.config import settings
    from app.domain.preview import office

    monkeypatch.setattr(settings, "office_render_endpoint", "http://render:8901")

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"report.docx": "first document"}, deliver="report.docx"
    )
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)
    artifact_id = _artifact_id(client, project_id)
    url = f"/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file"

    asked: list[tuple[str, str]] = []

    async def ask(raw, suffix, target, endpoint, timeout):
        asked.append((suffix, target))
        assert raw == b"first document"
        return b"%PDF-preview of the first document"

    monkeypatch.setattr(office, "_ask", ask)
    preview = client.get(url, params={"preview": True})
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.content == b"%PDF-preview of the first document"
    assert asked == [(".docx", ".pdf")]
    # 不带 preview 的那一次仍然是原来那份字节，一个字节都没被换掉。
    assert client.get(url).content == b"first document"


def test_an_old_sheet_is_served_as_a_workbook_not_as_a_pdf(client, monkeypatch):
    """`.xls` 是唯一一个「不改格式就没人画得出来」的表格：ExcelJS 读不了 BIFF。

    它转成 `.xlsx` 仍然是一张表，而不是一份 PDF —— 一页一页摊开的表没有了列
    和格子地址，那是这张表唯一能被人指着说话的东西。
    """
    from app.core.config import settings
    from app.domain.preview import office

    monkeypatch.setattr(settings, "office_render_endpoint", "http://render:8901")

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"预算.xls": "old binary"}, deliver="预算.xls"
    )
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)
    artifact_id = _artifact_id(client, project_id)

    async def ask(raw, suffix, target, endpoint, timeout):
        assert (suffix, target) == (".xls", ".xlsx")
        return b"PK\x03\x04as a workbook"

    monkeypatch.setattr(office, "_ask", ask)
    got = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file",
        params={"preview": True},
    )
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].endswith("spreadsheetml.sheet")
    assert got.content == b"PK\x03\x04as a workbook"


def test_the_renderer_being_absent_is_not_a_bad_file(client, monkeypatch):
    """这个部署没接渲染器，是一个会自己好的状态；这个文件转换不了，重试一百次
    也一样。两者读到的句子和前端给的状态都不能是同一条。"""
    from app.core.config import settings
    from app.domain.preview import office

    monkeypatch.setattr(settings, "office_render_endpoint", None)

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"report.docx": "first document"}, deliver="report.docx"
    )
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)
    artifact_id = _artifact_id(client, project_id)
    url = f"/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file"

    async def never(*args):
        raise AssertionError("没有渲染器就不该去敲它")

    monkeypatch.setattr(office, "_ask", never)
    assert client.get(url, params={"preview": True}).status_code == 503
    # 而下载一直是好的：预览没有，不等于这一版交出去的东西没有了。
    assert client.get(url).content == b"first document"


def test_a_file_too_big_to_preview_says_so_instead_of_a_status_code(
    client, monkeypatch, tmp_path
):
    from app.core.config import settings
    from app.domain.preview import office

    monkeypatch.setattr(settings, "office_render_endpoint", "http://render:8901")
    monkeypatch.setattr(office, "MAX_PREVIEW_BYTES", 8)

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"长报告.docx": "a document longer than eight bytes"},
        deliver="长报告.docx",
    )
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)
    artifact_id = _artifact_id(client, project_id)
    url = f"/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file"

    async def never(*args):
        raise AssertionError("太大就该在问渲染器之前被拦下")

    monkeypatch.setattr(office, "_ask", never)
    too_big = client.get(url, params={"preview": True})
    assert too_big.status_code == 422, too_big.text
    assert "无法生成预览" in too_big.text
    # 太大是预览的事，不是下载的事。
    assert client.get(url).status_code == 200


def test_a_version_is_behind_the_same_door_as_the_rest_of_the_manifest(
    client, monkeypatch
):
    """清单上的一版和项目里的其它东西同一个门槛：项目外的人连预览也拿不到。"""
    from app.core.config import settings
    from app.domain.preview import office

    monkeypatch.setattr(settings, "office_render_endpoint", "http://render:8901")
    monkeypatch.setattr(office, "_ask", _pdf())

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"report.docx": "first document"}, deliver="report.docx"
    )
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)
    artifact_id = _artifact_id(client, project_id)
    url = f"/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file"

    assert client.get(url, params={"preview": True}).status_code == 200, (
        "项目成员本来就该看得到"
    )
    assert (
        client.get(
            url,
            params={"preview": True},
            headers=session_auth_headers("mallory"),
        ).status_code
        == 403
    )
    assert client.get(url, headers=session_auth_headers("mallory")).status_code == 403


def test_handing_over_the_merge_itself_is_a_kind_of_its_own(client):
    """代码仓库交出去的是主干往前走一步 —— 没有可下载的东西，而这不是缺东西。"""
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(client, room_id)
    assert filed.status_code == 200, filed.text
    _accept(client, filed.json()["data"]["id"])

    # 交出去的是这次合并，所以落在项目那个仓库那一项上 —— 它跟项目同名，因为没有
    # 谁给它起过名字，平台自己认得出是哪一项。
    (version,) = _detail(client, project_id, _artifact_id(client, project_id, "P"))[
        "versions"
    ]
    assert version["kind"] == "merge"
    assert version["filename"] is None and version["url"] is None


def test_the_card_says_what_this_delivery_hands_over(client):
    """验收的人正在决定这一份要不要成为当前版本，所以卡上得有它。"""
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "待验收\n"},
        deliver="out/报告.pdf",
    )
    card_id = filed.json()["data"]["id"]

    cards = client.get(f"/topics/{room_id}/accept-card").json()["data"]["data"]
    card = next(c for c in cards if c["id"] == card_id)
    assert card["deliverable"] == {
        "kind": "file",
        "filename": "报告.pdf",
        "url": None,
    }


def test_a_path_that_is_not_there_files_no_card_and_claims_nothing(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(client, room_id, deliver="out/没有这个.pdf")
    assert filed.status_code >= 400

    assert client.get(f"/projects/{project_id}/artifacts").json()["data"]["data"] == []
    assert client.get(f"/topics/{room_id}/accept-card").json()["data"]["data"] == []


def test_a_file_and_an_address_at_once_is_refused(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "x\n"},
        deliver="out/报告.pdf",
        deliver_url="https://example.com/site",
    )
    assert filed.status_code >= 400
    assert "只能给一个" in filed.text


def test_an_address_that_is_not_one_is_refused(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(client, room_id, deliver_url="example.com/site")
    assert filed.status_code >= 400
    assert "网址" in filed.text


def test_a_file_over_the_ceiling_is_refused(client, monkeypatch):
    """上限管的是平台那块盘。上限本身是个参数，这里问的是超了会不会被拦住。"""
    monkeypatch.setattr(review_services, "_DELIVERABLE_MAX_BYTES", 8)
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "这一份超过了上限\n"},
        deliver="out/报告.pdf",
    )
    assert filed.status_code >= 400
    assert "上限" in filed.text
    assert client.get(f"/topics/{room_id}/accept-card").json()["data"]["data"] == []


def test_a_version_of_another_project_is_not_downloadable_here(client):
    """卡属于哪一项产物是服务端认的，不是 URL 里凑出来的。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"out/报告.pdf": "私有\n"}, deliver="out/报告.pdf"
    )
    _accept(client, filed.json()["data"]["id"])
    artifact_id = _artifact_id(client, project_id)
    (version,) = _detail(client, project_id, artifact_id)["versions"]

    other_project = _project(client)
    denied = client.get(
        f"/projects/{other_project}/artifacts/{artifact_id}"
        f"/versions/{version['card_id']}/file"
    )
    assert denied.status_code == 404


def test_a_card_that_belongs_to_another_artifact_is_not_this_version(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"out/报告.pdf": "x\n"}, deliver="out/报告.pdf"
    )
    _accept(client, filed.json()["data"]["id"])
    artifact_id = _artifact_id(client, project_id)

    missing = client.get(
        f"/projects/{project_id}/artifacts/{artifact_id}/versions/{uuid.uuid4()}/file"
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("field", ["deliver", "deliver_url"])
def test_saying_a_field_is_null_hands_over_the_merge_like_leaving_it_out(client, field):
    """显式写一个 null 和压根不写是同一件事：这次交出去的是合并本身。

    于是它也不用声明产物 —— 交出去的是项目那个仓库（那一半在
    `test_artifact_is_the_repository.py`）。这里问的只是 null 会不会被当成
    「交了一份空的」而走岔到另一条路上。
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = delivery_task(client, room_id)

    filed = client.post(
        f"/topics/{room_id}/tasks/{task.id}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            "change_subject": "chore(test): no artifact",
            "reviewer_handle": "alice",
            field: None,
        },
    )
    assert filed.status_code == 200, filed.text
    assert filed.json()["data"]["deliverable"]["kind"] == "merge"


def test_the_reviewer_can_take_the_file_before_accepting(client):
    """递卡之后、采纳之前就拿得到那一份。

    验收的人要审的正是这一份，而快照在递卡那一刻就落好了 —— 让他等采纳之后才看得
    到，等于让他先批准再检查。
    """
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(
        client,
        room_id,
        files={"out/结题报告.docx": "还没有人采纳的正文\n"},
        deliver="out/结题报告.docx",
    )
    assert filed.status_code == 200, filed.text
    card = filed.json()["data"]
    assert card["status"] == "pending"
    # 卡面要说得出这两样：这次动的是哪一项产物、交出去的是什么。
    assert card["artifact"]["name"] == "报告"
    assert card["deliverable"] == {
        "kind": "file",
        "filename": "结题报告.docx",
        "url": None,
    }

    got = client.get(f"/accept-cards/{card['id']}/deliverable")
    assert got.status_code == 200, got.text
    assert got.content == "还没有人采纳的正文\n".encode()

    # 项目外的人拿不到：这一份采纳之后就是《报告》的一版，和清单上那几版同一个
    # 门槛。
    denied = client.get(
        f"/accept-cards/{card['id']}/deliverable",
        headers=session_auth_headers("mallory"),
    )
    assert denied.status_code == 403


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param({"deliver_url": "https://site.example/r"}, id="link"),
        pytest.param({}, id="merge"),
    ],
)
def test_a_card_that_hands_over_no_file_has_nothing_to_take(client, declared):
    """交出去的是一个地址、或者就是这次合并：没有可下载的东西，而这不是缺东西。"""
    project_id = _project(client)
    room_id = _room(client, project_id)

    filed = _hand_over(client, room_id, **declared)
    assert filed.status_code == 200, filed.text
    card = filed.json()["data"]
    assert card["deliverable"]["filename"] is None

    got = client.get(f"/accept-cards/{card['id']}/deliverable")
    assert got.status_code == 404
    assert "不是一份文件" in got.text


def test_the_card_carries_the_version_it_would_become(client):
    """卡上的版号是这张卡自己那一版。

    版号是数出来的（数已采纳的卡），所以还没采纳的那一张不在这个数里面。卡面要写
    它采纳之后的号：人正在定的是「这一版要不要成为《报告》的当前版本」，写着前一
    版的号码等于把他要定的那件事写错。
    """
    project_id = _project(client)
    room_id = _room(client, project_id)

    first = _hand_over(
        client, room_id, files={"out/报告.pdf": "第一版\n"}, deliver="out/报告.pdf"
    )
    assert first.json()["data"]["artifact"]["version"] == 1

    accepted = _accept(client, first.json()["data"]["id"])
    # 采纳之后它就是那一版，号不再往前跳一格。
    assert accepted.json()["data"]["artifact"]["version"] == 1

    second = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "第二版\n"},
        deliver="out/报告.pdf",
        again=True,
    )
    assert second.json()["data"]["artifact"]["version"] == 2
