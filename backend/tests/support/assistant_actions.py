"""What the harness of a person's 芝士 does with an action that reaches others
(``run_platform_tool``): ask its gate whether to take it
(``/assistant/agent/review``), and once taken, make the calls the person's own
pages make for it (``/assistant/agent/plan``), as the person."""

from __future__ import annotations


def _as(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def review(client, token: str, act: dict) -> dict:
    got = client.post("/assistant/agent/review", json=act, headers=_as(token))
    assert got.status_code == 200, got.text
    return got.json()["data"]


def calls(client, token: str, act: dict) -> list[dict]:
    got = client.post("/assistant/agent/plan", json=act, headers=_as(token))
    assert got.status_code == 200, got.text
    return got.json()["data"]["calls"]


def carry_out(client, token: str, act: dict) -> list:
    """Make the action's calls in order, as its tool does: a later call's
    ``{prev}`` is what the one before it made. The responses, each checked."""
    answered, made = [], None
    for call in calls(client, token, act):
        path = call["path"] if made is None else call["path"].replace("{prev}", made)
        sent = client.request(
            call["method"], path, json=call.get("body"), headers=_as(token)
        )
        assert 200 <= sent.status_code < 300, sent.text
        answered.append(sent)
        data = sent.json().get("data") if sent.content else None
        made = str(data["id"]) if isinstance(data, dict) and "id" in data else None
    return answered


def take(client, token: str, act: dict) -> list:
    """The gate takes the action, and it is carried out."""
    reviewed = review(client, token, act)
    assert reviewed["allowed"] is True, reviewed
    return carry_out(client, token, act)
