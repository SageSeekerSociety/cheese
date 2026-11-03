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

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.agent_session.services import AgentSessionService
from app.domain.review import services as review_services
from app.domain.topic.models import Topic
from tests.conftest import seed_user
from tests.delivery import delivery_artifact, delivery_headers, delivery_task
from tests.integration.conftest import (
    join_project_team,
    mail,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept_pr import (
    _give_card_a_pr,
    _rendered_head,
    _rendered_revision,
)
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


def _project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    pid = r.json()["data"]["id"]
    # Every card here is accepted by alice. Accepting is a room decision:
    # since 2026-09-26 it requires membership (`_card_actor`), so the accepter
    # is a participant of the project these cards live in.
    join_project_team(client, pid, "alice")
    return pid


def _room(client, project_id: str, title: str = "做一个东西") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _place(client, task) -> None:
    """The task's own session takes the machine its working copy is on."""

    async def place():
        async with client.test_factory() as session:
            room = await session.get(Topic, task.room_id)
            await AgentSessionService(session).remember_place(
                conversation_id=task.id,
                agent_handle="cheese",
                work_lease={"kind": "device"},
                runtime_location={
                    "device_id": "test-device",
                    "channel": "central",
                    "resource_id": str(room.resource_id or room.id),
                },
                harness=harness_for(None),
            )
            await session.commit()

    asyncio.run(place())


def _hand_over(client, room_id: str, *, files=None, again=False, **declared):
    """递一张卡，可选地先把交付物放进这条活的工作目录里。

    `again`：这个房间的又一次交付。采纳会关闭那条活，所以第二次交付走的是新开
    的一条 —— 和真实情况一样（一条活交付一次）。
    """
    task = delivery_task(client, room_id, new=again)
    _place(client, task)
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
        f"/topics/{task.id}/accept-card",
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
        json={
            "decided_by": "alice",
            "head_sha": _rendered_head(client, card_id),
            "deliverable_revision": _rendered_revision(client, card_id),
        },
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
    from app.domain.preview import office
    from app.domain.preview.office import OfficeRenderUnavailable

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client, room_id, files={"report.docx": "first document"}, deliver="report.docx"
    )
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)
    artifact_id = _artifact_id(client, project_id)
    url = f"/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file"

    async def render(data, name, endpoint):
        assert data == b"first document"
        assert name == "report.docx"
        return b"%PDF-preview of the first document"

    monkeypatch.setattr(office, "render_to_pdf", render)
    preview = client.get(url, params={"preview_pdf": True})
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.content == b"%PDF-preview of the first document"
    assert client.get(url).content == b"first document"

    async def unavailable(*args):
        raise OfficeRenderUnavailable("文档预览服务暂时无法访问")

    monkeypatch.setattr(office, "render_to_pdf", unavailable)
    assert client.get(url, params={"preview_pdf": True}).status_code == 503
    assert client.get(url).content == b"first document"


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
    assert {k: card["deliverable"][k] for k in ("kind", "filename", "url")} == {
        "kind": "file",
        "filename": "报告.pdf",
        "url": None,
    }


def test_a_path_that_is_not_there_files_no_card_and_claims_nothing(client):
    project_id = _project(client)
    room_id = _room(client, project_id)

    # The task's copy is on its machine; the declared path is not in it.
    filed = _hand_over(
        client, room_id, files={"out/报告.pdf": "x\n"}, deliver="out/没有这个.pdf"
    )
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
        f"/topics/{task.id}/accept-card",
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
    assert {k: card["deliverable"][k] for k in ("kind", "filename", "url")} == {
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


def test_each_version_names_its_room_only_to_readers_of_that_room(client):
    project_id = _project(client)
    room_id = _room(client, project_id, "结题报告修订")
    card = _hand_over(
        client, room_id, files={"out/报告.pdf": "第一版\n"}, deliver="out/报告.pdf"
    )
    _accept(client, card.json()["data"]["id"])
    join_project_team(client, project_id, "bob")
    artifact_id = _artifact_id(client, project_id)

    def as_bob() -> dict:
        r = client.get(
            f"/projects/{project_id}/artifacts/{artifact_id}",
            headers=session_auth_headers("bob"),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["versions"][0]

    version = as_bob()
    assert version["room"] == {"id": room_id, "title": "结题报告修订"}
    assert version["bytes"] == len("第一版\n".encode())

    async def make_private():
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(room_id))
            room.is_private = True
            await session.commit()

    asyncio.run(make_private())
    assert as_bob()["room"] is None, "a private room's name reached a non-member"


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param(
            {
                "files": {"out/报告.pdf": "无分支提交的独立文件\n"},
                "deliver": "out/报告.pdf",
            },
            id="file",
        ),
        pytest.param(
            {"deliver_url": "https://example.com/standalone"},
            id="link",
        ),
    ],
)
def test_file_and_link_deliveries_succeed_and_accept_without_branch_commits_or_pr(
    client, declared
):
    """独立文件与链接交付不要求任务分支有 Git 提交，也不必开或合 PR。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = delivery_task(client, room_id, commit=False)
    _place(client, task)
    files = declared.get("files") or {}
    for name, content in files.items():
        path = client.test_machine_home / ".cheese/tasks" / str(task.id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    payload = {k: v for k, v in declared.items() if k != "files"}
    response = client.post(
        f"/topics/{task.id}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            "change_subject": "docs(report): deliver without repository commits",
            "reviewer_handle": "alice",
            "new_artifact": "独立产物",
            "about": "不进代码仓库的独立交付物",
            **payload,
        },
    )
    assert response.status_code == 200, response.text
    card = response.json()["data"]
    assert card["pr_number"] is None
    assert card["merge_state"]["state"] == "clean"
    assert card["merge_state"]["who"] == "human"
    assert card["merge_state"]["head_sha"] is None

    accepted = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={
            "decided_by": "alice",
            "head_sha": None,
            "deliverable_revision": card["deliverable"]["revision"],
        },
        headers=session_auth_headers("alice"),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"


def _put(client, task, name: str, content: str) -> None:
    path = client.test_machine_home / ".cheese/tasks" / str(task.id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _redeliver(client, task, headers, **body):
    return client.post(
        f"/topics/{task.id}/accept-card/redeliver", headers=headers, json=body
    )


def test_a_task_replaces_what_its_waiting_card_hands_over(client):
    """FB-104：卡还在等审阅，交付的人改了文件，要换的是卡上那一份 —— 不是再递一张，
    也不是去够那条只有房间里的人才能走的改验收人。

    换过之后验收人拿到的是新的那一份，旧的不再挂在这张卡上；审过旧那一份的批准
    不算数，验收人被叫回来再看一眼。"""
    reviewer = seed_user(client, "alice")
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"out/channel-row.html": "<p>第一稿</p>"},
        deliver="out/channel-row.html",
    )
    assert filed.status_code == 200, filed.text
    card = filed.json()["data"]
    approved = client.post(
        f"/accept-cards/{card['id']}/approve",
        json={"approver_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["approvals"] == ["alice"]
    heard_before = len(mail(client, reviewer, "ROOM_NOTICE"))

    task = delivery_task(client, room_id)
    _put(client, task, "out/channel-row-prototype.html", "<p>第二稿</p>")
    r = _redeliver(
        client,
        task,
        delivery_headers(client, room_id),
        deliver="out/channel-row-prototype.html",
    )

    assert r.status_code == 200, r.text
    now = r.json()["data"]
    assert now["id"] == card["id"]
    assert now["status"] == "pending"
    assert now["deliverable"]["filename"] == "channel-row-prototype.html"
    assert now["approvals"] == []
    got = client.get(f"/accept-cards/{card['id']}/deliverable")
    assert got.status_code == 200, got.text
    assert got.content == "<p>第二稿</p>".encode()
    assert len(mail(client, reviewer, "ROOM_NOTICE")) == heard_before + 1


def test_a_decided_card_is_not_redelivered(client):
    """采纳了的那一版已经是清单上的一版：换它的字节就是改写历史。要交新的一版，
    递一张新卡。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "采纳的那一份\n"},
        deliver="out/报告.pdf",
    )
    assert filed.status_code == 200, filed.text
    card_id = filed.json()["data"]["id"]
    _accept(client, card_id)

    task = delivery_task(client, room_id)
    _put(client, task, "out/报告2.pdf", "想偷换的一份\n")
    r = _redeliver(
        client, task, delivery_headers(client, room_id), deliver="out/报告2.pdf"
    )

    assert r.status_code == 409, r.text
    got = client.get(f"/accept-cards/{card_id}/deliverable")
    assert got.content == "采纳的那一份\n".encode()


def test_a_card_that_hands_over_the_merge_has_nothing_to_redeliver(client):
    """交合并的卡交的是分支：换它走 push-fix，挂一个文件上去只会让卡说两件事。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(client, room_id)
    assert filed.status_code == 200, filed.text

    task = delivery_task(client, room_id)
    r = _redeliver(
        client,
        task,
        delivery_headers(client, room_id),
        deliver_url="https://site.example/r",
    )

    assert r.status_code == 409, r.text
    card = client.get(f"/topics/{room_id}/accept-card").json()["data"]["data"][0]
    assert card["deliverable"]["kind"] == "merge"


def test_another_tasks_session_cannot_redeliver_this_tasks_card(client):
    """同一个房间里另一条任务的会话，碰不到这条任务的卡。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "这条任务交的\n"},
        deliver="out/报告.pdf",
    )
    assert filed.status_code == 200, filed.text
    card_id = filed.json()["data"]["id"]
    mine = delivery_task(client, room_id)
    other = delivery_task(client, room_id, new=True)
    _put(client, mine, "out/别人的.pdf", "另一条任务塞进来的\n")
    other_session = {
        "Authorization": f"Bearer {
            mint_scoped_token(project_id=str(other.project_id), topic_id=str(other.id))
        }"
    }

    r = _redeliver(client, mine, other_session, deliver="out/别人的.pdf")

    assert r.status_code == 403, r.text
    got = client.get(f"/accept-cards/{card_id}/deliverable")
    assert got.content == "这条任务交的\n".encode()


def test_accepting_after_a_redeliver_takes_a_fresh_look(client):
    """采纳的是人看过的那一份。他看着第一稿，交付的人换成了第二稿（连文件名都
    没变）：照着屏幕上那一版点采纳要被拒，卡还在等；重新看过再点才采纳。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"out/report.pdf": "第一稿\n"},
        deliver="out/report.pdf",
    )
    assert filed.status_code == 200, filed.text
    card_id = filed.json()["data"]["id"]
    seen = _rendered_revision(client, card_id)
    head = _rendered_head(client, card_id)

    task = delivery_task(client, room_id)
    _put(client, task, "out/report.pdf", "第二稿\n")
    r = _redeliver(
        client, task, delivery_headers(client, room_id), deliver="out/report.pdf"
    )
    assert r.status_code == 200, r.text

    stale = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "alice", "head_sha": head, "deliverable_revision": seen},
        headers=session_auth_headers("alice"),
    )
    assert stale.status_code == 422, stale.text
    (card,) = client.get(f"/topics/{room_id}/accept-card").json()["data"]["data"]
    assert card["status"] == "pending"

    _accept(client, card_id)
    got = client.get(f"/accept-cards/{card_id}/deliverable")
    assert got.content == "第二稿\n".encode()


def test_an_accept_that_lands_during_a_redeliver_keeps_what_it_accepted(
    client, monkeypatch
):
    """读机器上那一份要几秒，人正好在这几秒里采纳了：采纳的那一版不能被换掉，
    它的字节也不能被删。换的那一方拿到 409。"""
    from app.api.routes.accept import _accept_service
    from app.domain.review.services import cards as cards_mod

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "采纳的那一份\n"},
        deliver="out/报告.pdf",
    )
    assert filed.status_code == 200, filed.text
    card_id = filed.json()["data"]["id"]
    head = _rendered_head(client, card_id)
    seen = _rendered_revision(client, card_id)
    read = cards_mod._read_deliverable

    async def read_while_someone_accepts(*args, **kwargs):
        data = await read(*args, **kwargs)
        async with client.test_factory() as session:
            await _accept_service(session).accept(
                card_id=uuid.UUID(card_id),
                decided_by="alice",
                head_sha=head,
                deliverable_revision=seen,
            )
            await session.commit()
        return data

    monkeypatch.setattr(cards_mod, "_read_deliverable", read_while_someone_accepts)
    task = delivery_task(client, room_id)
    _put(client, task, "out/报告2.pdf", "想偷换的一份\n")
    r = _redeliver(
        client, task, delivery_headers(client, room_id), deliver="out/报告2.pdf"
    )

    assert r.status_code == 409, r.text
    (card,) = client.get(f"/topics/{room_id}/accept-card").json()["data"]["data"]
    assert card["status"] == "accepted"
    assert card["deliverable"]["filename"] == "报告.pdf"
    got = client.get(f"/accept-cards/{card_id}/deliverable")
    assert got.content == "采纳的那一份\n".encode()


def test_a_redeliver_during_an_accept_does_not_slip_in_unseen_bytes(
    client, monkeypatch
):
    """反过来的那个顺序：换交付物的请求已经锁住卡、正要写新的一份，人带着屏幕上
    那一版点了采纳。采纳要么等它写完、看出版本变了而拒绝，要么先落定、让它拿到
    409 —— 不能是「卡采纳了，挂着的却是没人看过的那一份」。"""
    import time

    from app.api.routes.accept import _accept_service
    from app.domain.library import service as library_service
    from app.domain.review.services import cards as cards_mod

    project_id = _project(client)
    room_id = _room(client, project_id)
    filed = _hand_over(
        client,
        room_id,
        files={"out/报告.pdf": "看过的那一份\n"},
        deliver="out/报告.pdf",
    )
    assert filed.status_code == 200, filed.text
    card_id = filed.json()["data"]["id"]
    task = delivery_task(client, room_id)
    head = _rendered_head(client, card_id)
    seen = _rendered_revision(client, card_id)
    read = cards_mod._read_deliverable
    write = library_service.write_artifact_snapshot
    loop = {}
    outcome = {}

    async def remember_the_loop(*args, **kwargs):
        loop["it"] = asyncio.get_running_loop()
        return await read(*args, **kwargs)

    async def accept_from_the_old_screen():
        async with client.test_factory() as session:
            try:
                card = await _accept_service(session).accept(
                    card_id=uuid.UUID(card_id),
                    decided_by="alice",
                    head_sha=head,
                    deliverable_revision=seen,
                )
                await session.commit()
                outcome["status"] = card.status.value
            except Exception as exc:  # the refusal is one of the two answers
                outcome["refused"] = repr(exc)

    def write_once_an_accept_is_under_way(*args, **kwargs):
        pending = asyncio.run_coroutine_threadsafe(
            accept_from_the_old_screen(), loop["it"]
        )
        try:
            pending.result(timeout=2)
        except Exception:  # it waits on the row this request holds
            pass
        return write(*args, **kwargs)

    monkeypatch.setattr(cards_mod, "_read_deliverable", remember_the_loop)
    monkeypatch.setattr(
        library_service, "write_artifact_snapshot", write_once_an_accept_is_under_way
    )
    _put(client, task, "out/报告2.pdf", "没看过的那一份\n")
    _redeliver(client, task, delivery_headers(client, room_id), deliver="out/报告2.pdf")
    for _ in range(100):
        if outcome:
            break
        time.sleep(0.05)

    got = client.get(f"/accept-cards/{card_id}/deliverable")
    assert outcome, "the accept never finished"
    assert not (
        outcome.get("status") == "accepted"
        and got.content == "没看过的那一份\n".encode()
    ), outcome


@pytest.mark.timeout(60)
def test_accepting_a_file_card_whose_pr_is_not_open_yet_answers(client):
    """交文件、分支上也有改动、PR 还没开的卡：采纳先替它开 PR。这一步用另一条连接
    把 PR 号写回卡上，所以采纳和换交付物之间的排队不能是这一行的锁 —— 否则那条
    连接等这次请求提交，这次请求等它写完，请求永远不返回。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = delivery_task(client, room_id)
    _place(client, task)
    _put(client, task, "out/报告.pdf", "正文\n")
    filed = client.post(
        f"/topics/{task.id}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            "change_subject": "docs(report): finalise the report",
            "reviewer_handle": "alice",
            "deliver": "out/报告.pdf",
            **delivery_artifact(client, room_id, hands_over=True),
        },
    )
    assert filed.status_code == 200, filed.text
    card = filed.json()["data"]
    assert card["pr_number"] is None

    r = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={
            "decided_by": "alice",
            "head_sha": card["merge_state"]["head_sha"],
            "deliverable_revision": _rendered_revision(client, card["id"]),
        },
        headers=session_auth_headers("alice"),
    )

    # 开出来的 PR 还没有任何人看过它的 head，所以这一次不合、让人重新看：要紧
    # 的是请求答了，而不是答了什么。
    assert r.status_code in (200, 422), r.text
