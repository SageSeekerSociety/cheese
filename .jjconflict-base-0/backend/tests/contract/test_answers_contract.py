import pytest
from httpx import AsyncClient


async def _create_question(client: AsyncClient) -> int:
    """Create a fresh question so answer contract checks don't depend on seed
    data (the test DB is truncated per-test)."""
    resp = await client.post(
        "/questions",
        json={
            "title": "Contract Q",
            "content": "Contract question body",
            "type": 0,
            "topics": [],
            "groupId": None,
            "bounty": 0,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


@pytest.mark.anyio
async def test_python_list_answers_shape(authed_client: AsyncClient) -> None:
    question_id = await _create_question(authed_client)
    resp = await authed_client.get(
        f"/questions/{question_id}/answers", params={"page_size": 10}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "answers" in data
    assert "page" in data


@pytest.mark.anyio
async def test_python_create_answer_shape(authed_client: AsyncClient) -> None:
    question_id = await _create_question(authed_client)
    resp = await authed_client.post(
        f"/questions/{question_id}/answers",
        json={"content": "answer"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body["data"]
