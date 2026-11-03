"""Who may do what to a question, its answers and their comments, over HTTP.

Every cell here is a request someone sends and the answer they get: the lowest
standing that may act is let through and the change reads back; the standing
just below is refused and nothing changes; a caller with no credentials is asked
to sign in. Cells that other files already pin (`test_questions.py`,
`test_answers.py`, `test_an_answer_belongs_to_its_question.py`,
`test_an_invitation_belongs_to_its_question.py`) are not repeated here.
"""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def _new_person(user_client: UserCreator, api_client: TestClient) -> tuple[int, dict]:
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    return user.user_id, {"Authorization": f"Bearer {token}"}


@dataclass
class Scene:
    """A question by `author`, answered by `answerer`, with a comment by
    `commenter` on the question and one on the answer, and an invitation from
    the author to `invitee`; `stranger` is signed in and has no part in any of it.
    """

    client: TestClient
    author: dict[str, str]
    answerer: dict[str, str]
    commenter: dict[str, str]
    invitee: dict[str, str]
    stranger: dict[str, str]
    question_id: int
    answer_id: int
    question_comment_id: int
    answer_comment_id: int
    invitation_id: int

    @property
    def q(self) -> str:
        return f"/questions/{self.question_id}"

    @property
    def a(self) -> str:
        return f"{self.q}/answers/{self.answer_id}"

    def question(self) -> dict:
        response = self.client.get(self.q, headers=self.author)
        assert response.status_code == 200, response.text
        return response.json()["data"]["question"]

    def answer(self) -> dict:
        response = self.client.get(self.a, headers=self.author)
        assert response.status_code == 200, response.text
        return response.json()["data"]["answer"]

    def question_comment_ids(self) -> list[int]:
        response = self.client.get(f"{self.q}/comments", headers=self.author)
        assert response.status_code == 200, response.text
        return [c["id"] for c in response.json()["data"]["comments"]]

    def answer_comment_ids(self) -> list[int]:
        response = self.client.get(f"{self.a}/comments", headers=self.author)
        assert response.status_code == 200, response.text
        return [c["id"] for c in response.json()["data"]["comments"]]

    def invitation_exists(self) -> bool:
        response = self.client.get(
            f"{self.q}/invitations/{self.invitation_id}", headers=self.author
        )
        assert response.status_code in (200, 404), response.text
        return response.status_code == 200


@pytest.fixture
def scene(api_client: TestClient, user_client: UserCreator) -> Scene:
    _, author = _new_person(user_client, api_client)
    _, answerer = _new_person(user_client, api_client)
    _, commenter = _new_person(user_client, api_client)
    invitee_id, invitee = _new_person(user_client, api_client)
    _, stranger = _new_person(user_client, api_client)

    created = api_client.post(
        "/questions",
        headers=author,
        json={
            "title": f"Who may act {unique_int(100000, 999999)}",
            "content": "谁能动这道题？",
            "type": 0,
            "topics": [],
        },
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["data"]["id"]

    answered = api_client.post(
        f"/questions/{question_id}/answers",
        headers=answerer,
        json={"content": "一个回答"},
    )
    assert answered.status_code == 201, answered.text
    answer_id = answered.json()["data"]["id"]

    on_question = api_client.post(
        f"/questions/{question_id}/comments",
        headers=commenter,
        json={"content": "题目下的一条评论"},
    )
    assert on_question.status_code == 201, on_question.text

    on_answer = api_client.post(
        f"/questions/{question_id}/answers/{answer_id}/comments",
        headers=commenter,
        json={"content": "回答下的一条评论"},
    )
    assert on_answer.status_code == 201, on_answer.text

    invited = api_client.post(
        f"/questions/{question_id}/invitations",
        headers=author,
        json={"user_id": invitee_id},
    )
    assert invited.status_code == 201, invited.text

    return Scene(
        client=api_client,
        author=author,
        answerer=answerer,
        commenter=commenter,
        invitee=invitee,
        stranger=stranger,
        question_id=question_id,
        answer_id=answer_id,
        question_comment_id=on_question.json()["data"]["comment"]["id"],
        answer_comment_id=on_answer.json()["data"]["comment"]["id"],
        invitation_id=invited.json()["data"]["invitationId"],
    )


# --- Accepting an answer belongs to the question's author ---------------------


def test_an_answers_author_cannot_accept_their_own_answer(scene: Scene):
    """Only the question's author picks the accepted answer; the answerer cannot."""
    response = scene.client.put(
        f"{scene.q}/acceptance",
        headers=scene.answerer,
        params={"answer_id": scene.answer_id},
    )
    assert response.status_code == 403, response.text
    assert scene.question()["accepted_answer"] is None


def test_only_the_question_author_can_withdraw_an_acceptance(scene: Scene):
    """Only the question's author can take back the accepted answer."""
    accepted = scene.client.put(
        f"{scene.q}/acceptance",
        headers=scene.author,
        params={"answer_id": scene.answer_id},
    )
    assert accepted.status_code == 200, accepted.text

    for who in (scene.answerer, scene.stranger):
        refused = scene.client.delete(f"{scene.q}/accept", headers=who)
        assert refused.status_code == 403, refused.text
        assert scene.question()["accepted_answer"]["id"] == scene.answer_id

    anonymous = scene.client.delete(f"{scene.q}/accept")
    assert anonymous.status_code == 401, anonymous.text
    assert scene.question()["accepted_answer"]["id"] == scene.answer_id

    withdrawn = scene.client.delete(f"{scene.q}/accept", headers=scene.author)
    assert withdrawn.status_code == 200, withdrawn.text
    assert scene.question()["accepted_answer"] is None


# --- Withdrawing an invitation belongs to the question's author ---------------


@pytest.mark.parametrize("who", ["invitee", "stranger"])
def test_only_the_question_author_can_withdraw_an_invitation(scene: Scene, who: str):
    """Neither the invited person nor a bystander can withdraw an invitation."""
    response = scene.client.delete(
        f"{scene.q}/invitations/{scene.invitation_id}",
        headers=getattr(scene, who),
    )
    assert response.status_code == 403, response.text
    assert scene.invitation_exists()


# --- An answer is its author's, not the question author's ---------------------


def test_a_question_author_cannot_edit_an_answer_to_it(scene: Scene):
    """Asking the question does not let you rewrite someone's answer to it."""
    before = scene.answer()["content"]
    response = scene.client.put(
        scene.a, headers=scene.author, json={"content": "改写别人的回答"}
    )
    assert response.status_code == 403, response.text
    assert scene.answer()["content"] == before


def test_a_question_author_cannot_delete_an_answer_to_it(scene: Scene):
    """Asking the question does not let you delete someone's answer to it."""
    response = scene.client.delete(scene.a, headers=scene.author)
    assert response.status_code == 403, response.text
    scene.answer()


# --- A comment is its author's alone --------------------------------------------


@pytest.mark.parametrize("who", ["author", "stranger"])
def test_only_its_author_can_delete_a_comment_on_a_question(scene: Scene, who: str):
    """Only a comment's author can delete it, even on someone else's question
    and even when the one asking is that question's author."""
    response = scene.client.delete(
        f"{scene.q}/comments/{scene.question_comment_id}",
        headers=getattr(scene, who),
    )
    assert response.status_code == 403, response.text
    assert scene.question_comment_id in scene.question_comment_ids()


def test_a_signed_out_caller_cannot_delete_a_comment_on_a_question(scene: Scene):
    """Deleting a question comment needs a signed-in caller."""
    response = scene.client.delete(f"{scene.q}/comments/{scene.question_comment_id}")
    assert response.status_code == 401, response.text
    assert scene.question_comment_id in scene.question_comment_ids()


@pytest.mark.parametrize("who", ["author", "answerer"])
def test_neither_the_question_nor_the_answer_author_can_delete_a_comment_on_it(
    scene: Scene, who: str
):
    """Owning the question or the answer does not let you delete another
    person's comment on that answer."""
    response = scene.client.delete(
        f"{scene.a}/comments/{scene.answer_comment_id}",
        headers=getattr(scene, who),
    )
    assert response.status_code == 403, response.text
    assert scene.answer_comment_id in scene.answer_comment_ids()


# --- Everything else is open to any signed-in person, and to no one signed out -


_SIGNED_IN_ONLY = [
    ("GET", "/questions/followed", None),
    ("PUT", "{q}/followers", None),
    ("POST", "{q}/followers", None),
    ("GET", "{q}/vote", None),
    ("POST", "{q}/vote", {"voteType": "POSITIVE"}),
    ("DELETE", "{q}/vote", None),
    ("GET", "{q}/comments", None),
    ("POST", "{q}/comments", {"content": "x"}),
    ("GET", "{a}/vote", None),
    ("POST", "{a}/vote", {"voteType": "POSITIVE"}),
    ("DELETE", "{a}/vote", None),
    ("GET", "{a}/comments", None),
    ("POST", "{a}/comments", {"content": "x"}),
    ("DELETE", "{a}/favorite", None),
    ("GET", "{q}/followers", None),
]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    _SIGNED_IN_ONLY,
    ids=[f"{m} {p}" for m, p, _ in _SIGNED_IN_ONLY],
)
def test_a_signed_out_caller_is_asked_to_sign_in(
    scene: Scene, method: str, path: str, body: dict | None
):
    """Reading or reacting to questions and answers needs a signed-in caller."""
    url = path.format(q=scene.q, a=scene.a)
    response = scene.client.request(method, url, json=body)
    assert response.status_code == 401, response.text


def test_any_signed_in_person_may_vote_on_a_question(scene: Scene):
    """Voting on a question is open to anyone signed in, not just its author."""
    voted = scene.client.post(
        f"{scene.q}/vote", headers=scene.stranger, json={"voteType": "POSITIVE"}
    )
    assert voted.status_code == 200, voted.text
    tally = scene.client.get(f"{scene.q}/vote", headers=scene.stranger)
    assert tally.status_code == 200, tally.text
    assert tally.json()["data"]["upvotes"] == 1

    removed = scene.client.delete(f"{scene.q}/vote", headers=scene.stranger)
    assert removed.status_code == 200, removed.text
    tally = scene.client.get(f"{scene.q}/vote", headers=scene.stranger)
    assert tally.json()["data"]["upvotes"] == 0


def test_any_signed_in_person_may_follow_a_question(scene: Scene):
    """Following a question is open to anyone signed in."""
    response = scene.client.put(f"{scene.q}/followers", headers=scene.stranger)
    assert response.status_code == 200, response.text
    followed = scene.client.get("/questions/followed", headers=scene.stranger)
    assert followed.status_code == 200, followed.text
    assert scene.question_id in [q["id"] for q in followed.json()["data"]["questions"]]


@pytest.mark.parametrize(
    "path", ["/questions/trending", "/questions/stats", "/questions/search-terms"]
)
def test_the_question_overview_is_public(api_client: TestClient, path: str):
    """Trending questions, totals and popular search terms need no sign-in."""
    response = api_client.get(path)
    assert response.status_code == 200, response.text


def test_withdrawing_an_acceptance_on_a_missing_question_finds_nothing(scene: Scene):
    """A question that does not exist answers 404, as accepting on it does —
    not 403, which would say there is something there to be refused."""
    response = scene.client.delete("/questions/99999999/accept", headers=scene.author)
    assert response.status_code == 404, response.text
