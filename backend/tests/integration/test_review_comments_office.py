"""Comments on a Word document, a workbook or a deck in a task's changes.

The rules a reviewer can state without reading the code:

- A comment on a document points at a page, a slide or a cell; one that names
  nothing the file has is refused.
- 退回 tells 芝士 where each comment points and the words it is about.
- When 芝士 hands the document over again, a comment whose words are still in
  it keeps its place; one whose words are gone is shown as gone.
- A document handed over again is compared with the version that was sent
  back, so what shows is what changed in answer to the comments.
"""

from tests.delivery import delivery_task, delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.integration.test_review_comments import (
    _comments,
    _hand_over,
    _instruction,
    _reject,
    _room,
)
from tests.machine_work import machine_commits
from tests.unit.test_office_compare import document, workbook

REPORT = "docs/结题报告.docx"
SHEET = "docs/问卷汇总.xlsx"


def _write(client, room, **fields):
    return client.post(
        f"/topics/{delivery_task_id(client, room)}/review-comments",
        json={"body": "和问卷汇总表对不上", **fields},
        headers=session_auth_headers("alice"),
    )


def _compare(client, room, path):
    r = client.get(
        f"/topics/{delivery_task_id(client, room)}/review-comparison",
        params={"path": path},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["comparison"]


def test_a_document_comment_names_a_page_a_cell_or_nothing(client, stub_hooks):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    machine_commits(
        task.project_id, task.id, {REPORT: document("完成全部任务的有 98 人。")}
    )
    _hand_over(client, room)

    page = _write(client, room, path=REPORT, place="p3", line_text="98 人")
    cell = _write(client, room, path=SHEET, place="汇总!C5", line_text="96")
    nowhere = _write(client, room, path=REPORT, place="汇总!C5", line_text="98 人")

    assert page.status_code == 200, page.text
    assert page.json()["data"]["place"] == "p3"
    assert cell.json()["data"]["place"] == "汇总!C5"
    assert nowhere.status_code == 422


def test_send_back_says_where_and_which_words(client, stub_hooks):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    machine_commits(
        task.project_id, task.id, {REPORT: document("完成全部任务的有 98 人。")}
    )
    card = _hand_over(client, room)
    comment = _write(client, room, path=REPORT, place="p3", line_text="98 人")

    _reject(client, card["id"], [comment.json()["data"]["id"]])

    told = _instruction(client, card["id"])
    assert f"{REPORT} 第 3 页" in told
    assert "「98 人」" in told
    assert "和问卷汇总表对不上" in told


def test_a_comment_keeps_its_page_while_its_words_are_there(client, stub_hooks):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    machine_commits(
        task.project_id,
        task.id,
        {REPORT: document("表 3-1 汇总了三组的平均用时。", "完成全部任务的有 98 人。")},
    )
    card = _hand_over(client, room)
    kept = _write(client, room, path=REPORT, place="p3", line_text="表 3-1 汇总了")
    gone = _write(client, room, path=REPORT, place="p3", line_text="98 人")
    _reject(client, card["id"], [kept.json()["data"]["id"], gone.json()["data"]["id"]])

    machine_commits(
        task.project_id,
        task.id,
        {REPORT: document("表 3-1 汇总了三组的平均用时和标准差。", "完成的有 96 人。")},
        "docs: answer the comments",
    )
    _hand_over(client, room)

    lines = {c["id"]: c["current_line"] for c in _comments(client, room)}
    assert lines[kept.json()["data"]["id"]] == 3
    assert lines[gone.json()["data"]["id"]] is None


def test_a_document_handed_over_again_is_read_against_what_was_sent_back(
    client, stub_hooks
):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    machine_commits(
        task.project_id,
        task.id,
        {REPORT: document("3.2 实验结果", "完成全部任务的有 98 人。")},
    )
    card = _hand_over(client, room)
    first = _compare(client, room, REPORT)
    # The first delivery is read against what the project had: nothing.
    assert first["against"] == "taken" and first["new_file"] is True

    comment = _write(client, room, path=REPORT, place="p3", line_text="98 人")
    _reject(client, card["id"], [comment.json()["data"]["id"]])
    machine_commits(
        task.project_id,
        task.id,
        {REPORT: document("3.2 实验结果", "完成全部任务的有 96 人。")},
        "docs: fix the count",
    )
    _hand_over(client, room)

    again = _compare(client, room, REPORT)
    assert again["against"] == "returned"
    (row,) = [r for r in again["rows"] if r["op"] != "same"]
    assert {"op": "delete", "text": "98"} in row["pieces"]
    assert {"op": "insert", "text": "96"} in row["pieces"]


def test_a_workbook_is_compared_by_cell(client, stub_hooks):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    machine_commits(task.project_id, task.id, {SHEET: workbook({"C5": "96"})})
    card = _hand_over(client, room)
    comment = _write(client, room, path=SHEET, place="汇总!C5", line_text="96")
    _reject(client, card["id"], [comment.json()["data"]["id"]])
    machine_commits(
        task.project_id, task.id, {SHEET: workbook({"C5": "95"})}, "docs: recount"
    )
    _hand_over(client, room)

    (sheet,) = _compare(client, room, SHEET)["sheets"]
    assert [(c["address"], c["before"], c["after"]) for c in sheet["cells"]] == [
        ("C5", "96", "95")
    ]


def test_a_text_file_has_no_document_comparison(client, stub_hooks):
    _pid, room = _room(client)
    delivery_task(client, room)
    _hand_over(client, room)

    assert (
        _compare(client, room, f"deliveries/{delivery_task_id(client, room)}.txt")
        is None
    )
