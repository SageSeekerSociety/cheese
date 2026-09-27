"""小测 —— 一门课里的一周，成员答一次，客观题当场判、主观题等管理员复核。

按浏览器会收到的状态码断言四件事：

1. **可见性跟着单元走**：单元没发布，这一周的小测对成员**不存在**（404，不是
   403）—— 不另造第二个发布开关；
2. **答案键从不发给成员**：成员的载荷里没有 ``answer`` 这一格，只有他自己的得分
   （截止前可以改答案重交，能拿到答案键就等于能抄）；
3. **判分界就在题型上**：客观题交卷即出分，简答进管理员的复核队列，判完这次作答
   才算判完；
4. **写的一次只有管理员能过**，门外人连这块板都看不到。

判据本身不在这里：管理员是谁问 ``app.auth.space_access``，可见性是
``quiz_services._require_published``。这里测的是它的可观测后果。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator
from tests.integration.test_space_units import (
    _auth,
    _create_unit,
    _join,
    _login,
    _new_board,
)


def _create_quiz(
    api_client: TestClient,
    board: dict,
    *,
    unit_id: int,
    title: str = "第 4 周小测",
    due_at: int | None = None,
    token: str | None = None,
):
    return api_client.post(
        f"/spaces/{board['space_id']}/units/{unit_id}/quiz",
        json={"title": title, "dueAt": due_at},
        headers=_auth(token or board["creator_token"]),
    )


def _quiz_id(
    api_client: TestClient, board: dict, unit_id: int, *, due_at: int | None = None
) -> int:
    resp = _create_quiz(api_client, board, unit_id=unit_id, due_at=due_at)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["quiz"]["id"]


def _add_question(
    api_client: TestClient,
    board: dict,
    *,
    quiz_id: int,
    kind: str,
    prompt: str = "题目",
    options: list[str] | None = None,
    answer=None,
    points: int = 2,
    token: str | None = None,
):
    return api_client.post(
        f"/spaces/{board['space_id']}/quizzes/{quiz_id}/questions",
        json={
            "kind": kind,
            "prompt": prompt,
            "options": options or [],
            "answer": answer,
            "points": points,
        },
        headers=_auth(token or board["creator_token"]),
    )


def _question_id(resp) -> int:
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["question"]["id"]


def _get_unit_quiz(api_client: TestClient, board: dict, unit_id: int, token: str):
    return api_client.get(
        f"/spaces/{board['space_id']}/units/{unit_id}/quiz", headers=_auth(token)
    )


def _submit(
    api_client: TestClient,
    board: dict,
    *,
    quiz_id: int,
    answers: dict[int, object],
    token: str,
):
    return api_client.put(
        f"/spaces/{board['space_id']}/quizzes/{quiz_id}/my-attempt",
        json={
            "answers": [
                {"questionId": question_id, "response": response}
                for question_id, response in answers.items()
            ]
        },
        headers=_auth(token),
    )


def _published_unit(api_client: TestClient, board: dict, *, week: int = 4) -> int:
    resp = _create_unit(
        api_client, board, week=week, title=f"第 {week} 周", published=True
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["unit"]["id"]


# --- 1. 可见性跟着单元走 -------------------------------------------------------


def test_the_quiz_does_not_exist_for_a_student_until_its_week_is_published(
    api_client: TestClient, user_client: UserCreator
):
    """先建未发布的单元 + 小测：成员查它得到 404，管理员自己看得到。

    然后发布那一周 —— 同一个成员立刻看得到，不用改任何别的开关。
    """
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _create_unit(
        api_client, board, week=4, title="函数", published=False
    ).json()["data"]["unit"]["id"]
    quiz_id = _quiz_id(api_client, board, unit)

    hidden = _get_unit_quiz(api_client, board, unit, student_token)
    assert hidden.status_code == 404, hidden.text
    by_id = api_client.get(
        f"/spaces/{board['space_id']}/quizzes/{quiz_id}", headers=_auth(student_token)
    )
    assert by_id.status_code == 404, by_id.text

    assert (
        api_client.patch(
            f"/spaces/{board['space_id']}/units/{unit}",
            json={"published": True},
            headers=_auth(board["creator_token"]),
        ).status_code
        == 200
    )
    visible = _get_unit_quiz(api_client, board, unit, student_token)
    assert visible.status_code == 200, visible.text
    assert visible.json()["data"]["quiz"]["id"] == quiz_id


def test_the_answer_key_never_reaches_a_student(
    api_client: TestClient, user_client: UserCreator
):
    """同一份卷子，管理员那份带 ``answer``，成员那份连这一格都没有。

    截止前可以改答案重交（见下一个用例），所以答案键一旦发出去，交卷就变成了抄。
    """
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _published_unit(api_client, board)
    quiz_id = _quiz_id(api_client, board, unit)
    _add_question(
        api_client,
        board,
        quiz_id=quiz_id,
        kind="SINGLE_CHOICE",
        options=["1", "2", "3"],
        answer=1,
    )

    student_view = _get_unit_quiz(api_client, board, unit, student_token).json()["data"]
    assert len(student_view["questions"]) == 1
    assert "answer" not in student_view["questions"][0]
    assert student_view["questions"][0]["options"] == ["1", "2", "3"]

    teacher_view = _get_unit_quiz(
        api_client, board, unit, board["creator_token"]
    ).json()["data"]
    assert teacher_view["questions"][0]["answer"] == 1


# --- 2. 判分界在题型上 ---------------------------------------------------------


def test_objective_answers_are_graded_on_submission_and_short_answers_wait(
    api_client: TestClient, user_client: UserCreator
):
    """一次交卷里三种题：客观题当场判完，简答留在队列里；管理员判完之后才算判完。"""
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _published_unit(api_client, board)
    quiz_id = _quiz_id(api_client, board, unit)
    choice = _question_id(
        _add_question(
            api_client,
            board,
            quiz_id=quiz_id,
            kind="SINGLE_CHOICE",
            options=["对", "错"],
            answer=0,
            points=2,
        )
    )
    blank = _question_id(
        _add_question(
            api_client,
            board,
            quiz_id=quiz_id,
            kind="FILL_BLANK",
            answer=["int", "integer"],
            points=3,
        )
    )
    written = _question_id(
        _add_question(
            api_client,
            board,
            quiz_id=quiz_id,
            kind="SHORT_ANSWER",
            prompt="说说你为什么这么写",
            answer="要点：先想边界条件",
            points=5,
        )
    )

    submitted = _submit(
        api_client,
        board,
        quiz_id=quiz_id,
        answers={choice: 0, blank: " Integer ", written: "因为要先想边界"},
        token=student_token,
    )
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()["data"]
    assert data["maxScore"] == 10
    assert data["myAttempt"]["score"] == 5  # 2 + 3，简答还没判
    assert data["myAttempt"]["pendingReview"] is True
    by_question = {row["questionId"]: row for row in data["myAnswers"]}
    assert by_question[choice]["awardedPoints"] == 2
    assert by_question[blank]["awardedPoints"] == 3
    assert by_question[written]["needsReview"] is True

    teacher_view = _get_unit_quiz(
        api_client, board, unit, board["creator_token"]
    ).json()["data"]
    assert len(teacher_view["submissions"]) == 1
    assert teacher_view["submissions"][0]["score"] == 5
    queue = teacher_view["reviewQueue"]
    assert [row["questionId"] for row in queue] == [written]
    assert queue[0]["response"] == "因为要先想边界"
    assert "nickname" in queue[0]["user"]

    graded = api_client.patch(
        f"/spaces/{board['space_id']}/quizzes/{quiz_id}/answers/{queue[0]['answerId']}",
        json={"points": 4, "comment": "思路对，边界再写细一点"},
        headers=_auth(board["creator_token"]),
    )
    assert graded.status_code == 200, graded.text

    after = _get_unit_quiz(api_client, board, unit, student_token).json()["data"]
    assert after["myAttempt"]["score"] == 9  # 2 + 3 + 4
    assert after["myAttempt"]["pendingReview"] is False
    by_question = {row["questionId"]: row for row in after["myAnswers"]}
    assert by_question[written]["awardedPoints"] == 4
    assert by_question[written]["comment"] == "思路对，边界再写细一点"


def test_a_second_submission_replaces_the_first_one(
    api_client: TestClient, user_client: UserCreator
):
    """截止前改答案重交是正常的：**一份作答被换掉**，不是多出一份。

    交两次之后管理员那里仍然只有一行 —— 「他到底交的哪一份」不该成为问题。
    """
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _published_unit(api_client, board)
    quiz_id = _quiz_id(api_client, board, unit)
    question = _question_id(
        _add_question(
            api_client,
            board,
            quiz_id=quiz_id,
            kind="SINGLE_CHOICE",
            options=["对", "错"],
            answer=1,
            points=4,
        )
    )

    first = _submit(
        api_client, board, quiz_id=quiz_id, answers={question: 0}, token=student_token
    )
    assert first.json()["data"]["myAttempt"]["score"] == 0

    second = _submit(
        api_client, board, quiz_id=quiz_id, answers={question: 1}, token=student_token
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["myAttempt"]["score"] == 4

    teacher_view = _get_unit_quiz(
        api_client, board, unit, board["creator_token"]
    ).json()["data"]
    assert len(teacher_view["submissions"]) == 1
    assert teacher_view["submissions"][0]["score"] == 4


def test_a_closed_quiz_refuses_answers(
    api_client: TestClient, user_client: UserCreator
):
    """过了截止就不能再交 —— 但看得见（他该知道自己错过了什么）。"""
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _published_unit(api_client, board)
    past = int(time.time() * 1000) - 60_000
    quiz_id = _quiz_id(api_client, board, unit, due_at=past)

    refused = _submit(
        api_client, board, quiz_id=quiz_id, answers={}, token=student_token
    )
    assert refused.status_code == 400, refused.text
    assert _get_unit_quiz(api_client, board, unit, student_token).status_code == 200


def test_an_objective_answer_has_nothing_to_review(
    api_client: TestClient, user_client: UserCreator
):
    """客观题交卷那一刻就判完了，所以它**不进复核队列** —— 管理员那一栏是空的。

    「已经判完的题不能改分」那条规则在 ``tests/unit/test_quiz_grading.py`` 里测，
    这里只看得到它的后果：队列里没有他。
    """
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _published_unit(api_client, board)
    quiz_id = _quiz_id(api_client, board, unit)
    question = _question_id(
        _add_question(
            api_client,
            board,
            quiz_id=quiz_id,
            kind="SINGLE_CHOICE",
            options=["对", "错"],
            answer=0,
            points=2,
        )
    )
    assert (
        _submit(
            api_client,
            board,
            quiz_id=quiz_id,
            answers={question: 0},
            token=student_token,
        ).status_code
        == 200
    )

    teacher_view = _get_unit_quiz(
        api_client, board, unit, board["creator_token"]
    ).json()["data"]
    assert teacher_view["reviewQueue"] == []
    assert teacher_view["submissions"][0]["score"] == 2
    assert teacher_view["submissions"][0]["gradedAt"] is not None


# --- 3. 形状错了要当场拒绝 -----------------------------------------------------


def test_a_question_whose_key_does_not_fit_its_kind_is_refused(
    api_client: TestClient, user_client: UserCreator
):
    """答案键形状错不会报错、只会让这题**永远判错**，所以宁可 400。"""
    board = _new_board(user_client, api_client)
    unit = _published_unit(api_client, board)
    quiz_id = _quiz_id(api_client, board, unit)

    out_of_range = _add_question(
        api_client,
        board,
        quiz_id=quiz_id,
        kind="SINGLE_CHOICE",
        options=["甲", "乙"],
        answer=5,
    )
    assert out_of_range.status_code == 400, out_of_range.text

    one_option = _add_question(
        api_client,
        board,
        quiz_id=quiz_id,
        kind="SINGLE_CHOICE",
        options=["甲"],
        answer=0,
    )
    assert one_option.status_code == 400, one_option.text

    no_accepted_answer = _add_question(
        api_client, board, quiz_id=quiz_id, kind="FILL_BLANK", answer=[]
    )
    assert no_accepted_answer.status_code == 400, no_accepted_answer.text

    unknown_kind = _add_question(
        api_client, board, quiz_id=quiz_id, kind="ESSAY", answer="x"
    )
    assert unknown_kind.status_code == 400, unknown_kind.text

    not_a_bool = _add_question(
        api_client, board, quiz_id=quiz_id, kind="TRUE_FALSE", answer="yes"
    )
    assert not_a_bool.status_code == 400, not_a_bool.text


def test_a_week_holds_one_quiz(api_client: TestClient, user_client: UserCreator):
    board = _new_board(user_client, api_client)
    unit = _published_unit(api_client, board)
    _quiz_id(api_client, board, unit)

    second = _create_quiz(api_client, board, unit_id=unit, title="又一次")
    assert second.status_code == 400, second.text


# --- 4. 谁看得到、谁能写 -------------------------------------------------------


def test_only_the_teacher_writes_and_only_members_read(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    unit = _published_unit(api_client, board)
    quiz_id = _quiz_id(api_client, board, unit)

    as_student = _create_quiz(
        api_client, board, unit_id=unit, title="成员偷偷建一个", token=student_token
    )
    assert as_student.status_code == 403, as_student.text
    assert (
        _add_question(
            api_client,
            board,
            quiz_id=quiz_id,
            kind="TRUE_FALSE",
            answer=True,
            token=student_token,
        ).status_code
        == 403
    )

    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)
    outside = api_client.get(
        f"/spaces/{board['space_id']}/quizzes/{quiz_id}", headers=_auth(outsider_token)
    )
    assert outside.status_code == 404, outside.text


def test_the_unit_list_says_which_week_has_a_quiz(
    api_client: TestClient, user_client: UserCreator
):
    """单元列表自带 ``quizId`` —— 成员首页靠它决定要不要给「本周有小测」那个入口。"""
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    first = _published_unit(api_client, board, week=3)
    second = _published_unit(api_client, board, week=4)
    quiz_id = _quiz_id(api_client, board, second)

    units = api_client.get(
        f"/spaces/{board['space_id']}/units", headers=_auth(student_token)
    ).json()["data"]["units"]
    by_week = {unit["week"]: unit for unit in units}
    assert by_week[4]["quizId"] == quiz_id
    assert by_week[3]["quizId"] is None
    assert first != second
