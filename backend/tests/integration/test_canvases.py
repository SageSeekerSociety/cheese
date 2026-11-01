"""A canvas is a block of one document, and answers to that document.

Whoever may read the document reads its canvas; whoever may write the document
changes it; nobody else does either. Copying the document makes a canvas of the
copy's own. 芝士 reads a canvas as an outline of its cards and lines and changes
it with the same requests a person's board makes, and what it changes is what
the document's readers see.
"""

import importlib.util
import re
import uuid
from collections import Counter
from importlib.machinery import SourceFileLoader
from pathlib import Path

from sqlalchemy import event

from app.domain.agent.harness.channel import mint_session_token
from app.domain.agent.turn.intake.preparation import living_doc_text
from app.domain.canvas.services import settle
from app.domain.living_doc import collab
from app.domain.room_task.place import PlaceResolver
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    new_project,
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.living_doc import document_of

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"

ALICE = session_auth_headers("alice")
BOB = session_auth_headers("bob")
MALLORY = session_auth_headers("mallory")

BLOCK = ":::canvas new 项目地图\n各件事和它们之间的关系。\n:::"


def _load():
    loader = SourceFileLoader("cheese_canvas_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


def _project(client) -> str:
    """dave's project; alice and bob are on its team, mallory is not."""
    for who in ("alice", "bob", "dave", "mallory"):
        seed_user(client, who)
    pid = post_project(client, json={"name": "P"}, owner="dave").json()["data"]["id"]
    for handle in ("alice", "bob"):
        join_project_team(client, pid, handle)
    return pid


def _channel(client, pid: str, *, private: bool = False) -> str:
    made = client.post(
        "/topics",
        json={"project_id": pid, "title": "频道", "members_only": private},
        headers=ALICE,
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def _write(client, doc: str, content: str, headers=ALICE, version: int = 0) -> dict:
    put = client.put(
        f"/documents/{doc}",
        json={"content": content, "expected_version": version},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    return put.json()


def _content(client, doc: str, headers=ALICE) -> str:
    return client.get(f"/documents/{doc}", headers=headers).json()["data"]["content"]


def _task_canvas(client, *, private: bool = False) -> tuple[str, str, str, str]:
    """alice's task with a canvas in its living document: (project, task,
    document, canvas)."""
    pid = _project(client)
    task = open_task(client, _channel(client, pid, private=private), owner="alice")
    doc = document_of(client, task["id"], headers=ALICE)
    [made] = _write(client, doc, f"目标在这里。\n\n{BLOCK}")["canvases"]
    assert made["title"] == "项目地图"
    return pid, task["id"], doc, made["id"]


def _change(client, canvas: str, changes: list[dict], headers=ALICE):
    return client.post(
        f"/canvases/{canvas}/changes", json={"changes": changes}, headers=headers
    )


def _board(client, canvas: str, headers=ALICE):
    return client.get(f"/canvases/{canvas}", headers=headers)


def test_the_block_names_the_canvas_made_for_it(client):
    _, _, doc, canvas = _task_canvas(client)
    assert _content(client, doc) == (
        f"目标在这里。\n\n:::canvas {canvas} 项目地图\n各件事和它们之间的关系。\n:::"
    )
    board = _board(client, canvas).json()["data"]
    assert board["document_id"] == doc
    assert board["cards"] == [] and board["edges"] == []


def test_whoever_reads_the_document_reads_its_canvas_and_nobody_else(client):
    _, _, _, canvas = _task_canvas(client)
    added = _change(client, canvas, [{"op": "add", "content": "上线新版搜索"}])
    assert added.status_code == 200, added.text

    for reader in (ALICE, BOB):
        seen = _board(client, canvas, reader)
        assert seen.status_code == 200, seen.text
        assert [c["content"] for c in seen.json()["data"]["cards"]] == ["上线新版搜索"]
    refused = _board(client, canvas, MALLORY)
    assert refused.status_code in (403, 404)
    assert "上线新版搜索" not in refused.text
    assert client.get(f"/canvases/{canvas}/text", headers=MALLORY).status_code in (
        403,
        404,
    )


def test_a_private_channels_canvas_is_its_peoples_only(client):
    _, _, _, canvas = _task_canvas(client, private=True)
    _change(client, canvas, [{"op": "add", "content": "只给频道里的人看"}])

    assert _board(client, canvas, ALICE).status_code == 200
    # bob is in the project but not in the channel.
    refused = _board(client, canvas, BOB)
    assert refused.status_code in (403, 404)
    assert "只给频道里的人看" not in refused.text


def test_only_who_may_write_the_document_changes_its_canvas(client):
    _, _, _, canvas = _task_canvas(client)
    # bob reads alice's task's document but does not work the task.
    for who in (BOB, MALLORY):
        refused = _change(
            client, canvas, [{"op": "add", "content": "我也来加一个"}], who
        )
        assert refused.status_code in (403, 404), refused.text
        moved = client.patch(f"/canvases/{canvas}", json={"height": 600}, headers=who)
        assert moved.status_code in (403, 404), moved.text
    board = _board(client, canvas, ALICE).json()["data"]
    assert board["cards"] == [] and board["height"] is None
    assert _board(client, canvas, BOB).json()["data"]["writable"] is False
    assert _board(client, canvas, ALICE).json()["data"]["writable"] is True


def test_a_moved_card_is_where_it_was_left_when_the_board_is_read_again(client):
    _, _, _, canvas = _task_canvas(client)
    _change(client, canvas, [{"op": "add", "content": "目标"}])
    moved = _change(
        client, canvas, [{"op": "move", "card": 1, "x": 320, "y": -40, "width": 260}]
    )
    assert moved.status_code == 200, moved.text

    [card] = _board(client, canvas, BOB).json()["data"]["cards"]
    assert (card["x"], card["y"], card["width"]) == (320, -40, 260)
    assert card["updated_by"] == "alice"


def test_changes_are_made_all_or_none(client):
    _, _, _, canvas = _task_canvas(client)
    refused = _change(
        client,
        canvas,
        [
            {"op": "add", "ref": "a", "content": "第一件"},
            {"op": "connect", "from": "a", "to": 9},
        ],
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["data"]["index"] == 1
    assert _board(client, canvas).json()["data"]["cards"] == []


def test_a_copied_document_has_a_canvas_of_its_own(client):
    pid, _, doc, canvas = _task_canvas(client)
    _change(client, canvas, [{"op": "add", "content": "原来的格子"}])

    copied = client.post(
        f"/projects/{pid}/documents", json={"copy_of": doc}, headers=ALICE
    )
    assert copied.status_code == 200, copied.text
    copy_doc = copied.json()["data"]["id"]
    [copy] = [
        line.split()[1]
        for line in _content(client, copy_doc).splitlines()
        if line.startswith(":::canvas ")
    ]
    assert copy != canvas
    assert [c["content"] for c in _board(client, copy).json()["data"]["cards"]] == [
        "原来的格子"
    ]

    _change(client, copy, [{"op": "update", "card": 1, "content": "副本里改过"}])
    _change(client, copy, [{"op": "add", "content": "副本里新加的"}])

    original = _board(client, canvas).json()["data"]["cards"]
    assert [c["content"] for c in original] == ["原来的格子"]


def test_a_block_written_twice_into_a_document_is_two_canvases(client):
    _, _, doc, canvas = _task_canvas(client)
    _change(client, canvas, [{"op": "add", "content": "原来的格子"}])
    text = _content(client, doc)
    block = text[text.index(":::canvas") :]

    _write(client, doc, f"{text}\n\n{block}", version=1)

    refs = [
        line.split()[1]
        for line in _content(client, doc).splitlines()
        if line.startswith(":::canvas ")
    ]
    assert refs[0] == canvas and refs[1] != canvas
    _change(client, refs[1], [{"op": "delete", "card": 1}])
    assert len(_board(client, canvas).json()["data"]["cards"]) == 1


def test_a_canvas_of_a_document_the_writer_cannot_read_is_refused(client):
    pid, _, _, canvas = _task_canvas(client, private=True)
    # bob's own task, in a channel he is in; alice's canvas is in a private
    # channel he is not in.
    other = open_task(client, _channel(client, pid), owner="bob")
    doc = document_of(client, other["id"], headers=BOB)
    refused = client.put(
        f"/documents/{doc}",
        json={"content": f":::canvas {canvas} 别人的\n:::", "expected_version": 0},
        headers=BOB,
    )
    assert refused.status_code == 422, refused.text
    assert _board(client, canvas, ALICE).json()["data"]["document_id"] != doc


def _host(client, project: str, task: str):
    class Host:
        environ = {"CHEESE_TOPIC": task, "CHEESE_PROJECT": project}
        headers = {
            "Authorization": f"Bearer {
                mint_session_token(project, task, room_agent_seat(client, task))
            }"
        }
        doc_versions: dict = {}

        def request(self, plan):
            response = client.request(
                plan["method"],
                plan["path"],
                json=plan.get("body"),
                headers=self.headers,
            )
            if not 200 <= response.status_code < 300:
                raise cheese.PlatformHTTPError(response.status_code, response.text)
            return response.json()

    return Host()


def test_what_cheese_changes_on_a_canvas_its_readers_see(client):
    pid, task, doc, canvas = _task_canvas(client)
    host = _host(client, pid, task)

    said = cheese.run_platform_tool(
        "cheese_canvas",
        {
            "action": "edit",
            "canvas": canvas,
            "changes": [
                {"op": "add", "ref": "goal", "content": "十月上线新版搜索"},
                {"op": "add", "ref": "index", "content": "重建索引\n  - 先停写入"},
                {"op": "connect", "from": "index", "to": "goal", "label": "做完才能"},
            ],
        },
        host,
    )
    assert "[1]（goal）" in said and "[2]（index）" in said

    board = _board(client, canvas, BOB).json()["data"]
    assert [c["content"] for c in board["cards"]] == [
        "十月上线新版搜索",
        "重建索引\n  - 先停写入",
    ]
    [line] = board["edges"]
    assert line["label"] == "做完才能"
    assert {"type": "state", "resource": "canvas", "canvas": canvas} in (
        client.collab.told[collab.document_name(uuid.UUID(doc))]
    )


def test_the_canvas_reads_as_its_cards_and_the_lines_between_them(client):
    pid, task, _, canvas = _task_canvas(client)
    _change(
        client,
        canvas,
        [
            {"op": "add", "ref": "goal", "content": "十月上线新版搜索"},
            {"op": "add", "ref": "index", "content": "重建索引"},
            {"op": "add", "content": "便签：周五前定方案"},
            {"op": "connect", "from": "index", "to": "goal", "label": "做完才能"},
        ],
    )
    text = cheese.run_platform_tool(
        "cheese_canvas", {"action": "get", "canvas": canvas}, _host(client, pid, task)
    )

    assert "《项目地图》" in text
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if "重建索引" in line)
    assert "十月上线新版搜索" in lines[index + 1]
    assert "做完才能" in lines[index + 1]
    assert lines[index + 1].startswith("  ")
    assert any("便签：周五前定方案" in line for line in lines)
    # alice, a person, changed these last: a teammate is told so.
    assert "@alice 最后改过" in text


def test_a_card_names_its_task_to_those_who_may_see_the_task(client):
    pid, task, _, canvas = _task_canvas(client)
    hidden = open_task(client, _channel(client, pid, private=True), "私密的事")
    shown = open_task(client, _channel(client, pid), "公开的事")
    added = _change(
        client,
        canvas,
        [
            {"op": "add", "content": "看得见", "task": str(shown["number"])},
            {"op": "add", "content": "看不见", "task": hidden["id"]},
        ],
    )
    assert added.status_code == 200, added.text

    for_bob = _board(client, canvas, BOB).json()["data"]["cards"]
    assert for_bob[0]["task"]["title"] == "公开的事"
    assert for_bob[0]["task"]["column"]
    assert for_bob[1]["task"] is None and for_bob[1]["task_hidden"] is True
    assert "私密的事" not in _board(client, canvas, BOB).text
    assert _board(client, canvas, ALICE).json()["data"]["cards"][1]["task"][
        "title"
    ] == ("私密的事")
    text = client.get(f"/canvases/{canvas}/text", headers=BOB).json()["data"]["text"]
    assert "公开的事" in text and "私密的事" not in text


def _teammate_of(client, pid: str, room: str) -> dict:
    """The saved teammate seated in this channel, as the project lists it."""
    seat = room_agent_seat(client, room)
    agents = client.get(f"/projects/{pid}/agents", headers=ALICE).json()["data"]
    return next(a for a in agents["data"] if a["seat_handle"] == seat)


def test_a_card_names_the_teammate_working_its_task_by_its_given_name(client):
    """A card's phrase can name the teammate (「{agent}在做」). A project that
    renamed it reads that name; one that never did reads 「default」, which a
    screen says in its reader's language rather than as a stored 芝士."""
    pid, _, _, canvas = _task_canvas(client)
    room = _channel(client, pid)
    shown = open_task(client, room, "公开的事")
    assert (
        _change(
            client, canvas, [{"op": "add", "content": "x", "task": shown["id"]}]
        ).status_code
        == 200
    )

    [card] = _board(client, canvas).json()["data"]["cards"]
    assert card["task"]["agent_name_source"] == "default"

    agent = _teammate_of(client, pid, room)
    renamed = client.put(
        f"/projects/{pid}/agents/{agent['id']}",
        json={"display_name": "Nova"},
        headers=session_auth_headers("dave"),
    )
    assert renamed.status_code == 200, renamed.text

    [card] = _board(client, canvas).json()["data"]["cards"]
    assert card["task"]["agent_name"] == "Nova"
    assert card["task"]["agent_name_source"] == "human"


def test_reading_the_document_brings_its_canvas_as_text(client):
    pid, task, _, canvas = _task_canvas(client)
    _change(client, canvas, [{"op": "add", "content": "十月上线新版搜索"}])
    host = _host(client, pid, task)

    read = cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)
    body, _, canvases = read.partition("--- 文档里的画布")
    assert f":::canvas {canvas} 项目地图" in body
    assert "十月上线新版搜索" not in body
    assert "十月上线新版搜索" in canvases

    async def prompt_text() -> str | None:
        async with client.test_request_factory() as session:
            place = await PlaceResolver(session).conversation(uuid.UUID(task))
            assert place is not None
            return await living_doc_text(session, place, needs_place=True)

    carried = client.portal.call(prompt_text)
    assert carried is not None
    assert f":::canvas {canvas} 项目地图" in carried
    assert "十月上线新版搜索" in carried.split("文档里的画布", 1)[1]


def test_cheese_writing_a_new_canvas_block_is_told_which_canvas_it_got(client):
    pid, task, doc, _ = _task_canvas(client)
    host = _host(client, pid, task)
    cheese.run_platform_tool("cheese_doc", {"action": "get"}, host)

    said = cheese.run_platform_tool(
        "cheese_doc",
        {
            "action": "set",
            "content": f"{_content(client, doc)}\n\n:::canvas new 依赖\n:::",
        },
        host,
    )

    made = said.split("新建了画布《依赖》，编号 ", 1)[1].split("：", 1)[0]
    assert f":::canvas {made} 依赖" in _content(client, doc)
    assert _board(client, made).status_code == 200


def test_settle_queries_canvas_rows_once_regardless_of_block_count(client):
    project, _, doc, _ = _task_canvas(client)
    content = "\n\n".join(":::canvas new 画布\n:::" for _ in range(12))
    created = _write(client, doc, content, version=1)
    canvas_ids = [uuid.UUID(canvas["id"]) for canvas in created["canvases"]]

    async def _readable(_document_id: uuid.UUID) -> bool:
        return True

    async def query_count(count: int) -> int:
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        engine = client.test_app_engine.sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            async with client.test_request_factory() as session:
                await settle(
                    session,
                    "\n\n".join(
                        f":::canvas {canvas_id} 画布\n:::"
                        for canvas_id in canvas_ids[:count]
                    ),
                    document_id=uuid.UUID(doc),
                    project_id=uuid.UUID(project),
                    actor="alice",
                    keep=Counter(canvas_ids[:count]),
                    readable=_readable,
                )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(statements)

    three = client.portal.call(query_count, 3)
    twelve = client.portal.call(query_count, 12)
    assert three == twelve


def _task_reads(statements: list[str]) -> list[str]:
    """The statements that looked a task up by the number a card names: one
    for a change however many cards it names, when it reads them at once."""
    return [
        statement
        for statement in statements
        if re.search(r"tasks\.number\s*(?:=|IN\b)", statement)
        and "FROM tasks" in statement
    ]


def test_a_change_naming_thirty_tasks_reads_them_once(client):
    pid, _, _, canvas = _task_canvas(client)
    room = _channel(client, pid)
    tasks = [open_task(client, room, f"第 {i:02d} 件") for i in range(30)]

    def change(cards: int) -> tuple[list[str], dict]:
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()))

        engine = client.test_app_engine.sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = _change(
                client,
                canvas,
                [
                    {
                        "op": "add",
                        "content": f"第 {i:02d} 张",
                        # A number, and the same number written as an address.
                        "task": f"#{tasks[i]['number']}"
                        if i % 2
                        else str(tasks[i]["number"]),
                    }
                    for i in range(cards)
                ],
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return statements, response.json()["data"]

    three, _ = change(3)
    thirty, state = change(30)

    # One read of the tasks table whether three cards name tasks or thirty —
    # and the request as a whole does not grow with the board either.
    assert len(_task_reads(thirty)) == 1, _task_reads(thirty)
    assert len(_task_reads(three)) == 1, _task_reads(three)
    assert len(three) == len(thirty)

    # Every card points at the task it named.
    assert [card["task"]["id"] for card in state["cards"]] == [
        task["id"] for task in tasks[:3]
    ] + [task["id"] for task in tasks]


def test_a_card_naming_a_task_the_caller_cannot_point_at_refuses_the_change(client):
    """No such task, and a task in a channel the reader is not in: either one
    refuses the whole change list, naming the ref, as it did when each card
    was resolved on its own."""
    pid, _, _, _ = _task_canvas(client)
    hidden = open_task(client, _channel(client, pid, private=True), "私密的事")
    mine = open_task(client, _channel(client, pid), "我的事", owner="bob")
    doc = document_of(client, mine["id"], headers=BOB)
    [made] = _write(client, doc, BLOCK, headers=BOB)["canvases"]
    canvas = made["id"]

    for ref in ("9999", hidden["id"], "不是任务"):
        refused = _change(
            client,
            canvas,
            [
                {"op": "add", "content": "看得见", "task": str(mine["number"])},
                {"op": "add", "content": "看不见", "task": ref},
            ],
            BOB,
        )
        assert refused.status_code == 422, refused.text
        assert ref in refused.text
        assert _board(client, canvas, BOB).json()["data"]["cards"] == []


def test_a_task_of_another_project_is_not_a_task_here(client):
    """A card names a task of this document's project, or none: another
    project's id is one this canvas may not point at."""
    pid, _, _, canvas = _task_canvas(client)
    other = new_project(client, name="Q", owner="dave")
    join_project_team(client, other["id"], "alice")
    elsewhere = open_task(client, _channel(client, other["id"]), "别处的事")

    refused = _change(
        client, canvas, [{"op": "add", "content": "跨项目", "task": elsewhere["id"]}]
    )
    assert refused.status_code == 422, refused.text
    assert elsewhere["id"] in refused.text
    assert _board(client, canvas).json()["data"]["cards"] == []


def test_an_update_sets_and_clears_a_cards_task(client):
    pid, _, _, canvas = _task_canvas(client)
    task = open_task(client, _channel(client, pid), "要挂上去的事")
    _change(client, canvas, [{"op": "add", "content": "一张卡"}])

    linked = _change(
        client, canvas, [{"op": "update", "card": 1, "task": str(task["number"])}]
    )
    assert linked.status_code == 200, linked.text
    assert _board(client, canvas).json()["data"]["cards"][0]["task"]["id"] == task["id"]

    cleared = _change(client, canvas, [{"op": "update", "card": 1, "task": None}])
    assert cleared.status_code == 200, cleared.text
    assert _board(client, canvas).json()["data"]["cards"][0]["task"] is None


def test_a_ref_that_names_nothing_is_refused_before_it_can_crash(client):
    """A ref that is no number and no id is refused, and the change before it
    is still the one named: resolving every ref at once does not move an
    earlier change's refusal behind a bad reference."""
    _, _, _, canvas = _task_canvas(client)
    _change(client, canvas, [{"op": "add", "content": "第一张"}])

    # "①" passes isdigit but no int(); 2**31 is one past what the number
    # column holds — the smallest ref a wrong bound would let through.
    for ref in ("①", str(2**31), "9999999999"):
        refused = _change(
            client,
            canvas,
            [
                {"op": "move", "card": 99, "x": 1, "y": 1},
                {"op": "add", "content": "越界的引用", "task": ref},
            ],
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["data"]["index"] == 0
