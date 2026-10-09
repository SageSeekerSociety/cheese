"""Who answers is who was addressed.

A room is a collaboration space: it holds members, several of which may be
agents. Addressing one is how a person picks who answers, and it has to be how
a room picks too — the alternative is the room pointing at an agent, and then
@-ing the second teammate runs the first one's turn (#1192).
"""

import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client, name: str = "Two teammates") -> str:
    r = post_project(client, json={"name": name}, owner="alice")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _agent(client, project: str, handle: str, display: str) -> dict:
    r = client.post(
        f"/projects/{project}/agents",
        json={"handle": handle, "display_name": display},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _seat(client, topic: str, handle: str, actor: str = "alice") -> None:
    r = client.post(
        f"/topics/{topic}/members",
        json={"handle": handle, "role": "member", "actor": actor},
        headers=session_auth_headers(actor),
    )
    assert r.status_code == 200, r.text


def _room(client, project: str, title: str = "Room") -> str:
    """这间房。alice 建的，所以她也在名册上。"""
    r = client.post(
        "/topics",
        json={"project_id": project, "title": title},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _teammate_says(client, project: str, conversation: str, seat: str) -> str:
    """队友自己在对话里说了一句 —— 落一条真消息，不走轮次。返回它的 id，好让
    人回复它。"""

    async def go() -> str:
        async with client.test_factory() as session:
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(project),
                conversation_id=uuid.UUID(conversation),
                author=seat,
                author_type=AuthorType.participant,
                content="这条我来答",
                kind=BlockKind.message,
            )
            await session.commit()
            return str(block.id)

    return client.portal.call(go)


def _recipient_of(
    client, conversation: str, content: str, reply_to: str | None = None
) -> dict:
    """alice 说了这一句之后，平台记下的收件人 —— 谁的轮次会被它起。"""
    body: dict = {"content": content}
    if reply_to is not None:
        body["reply_to"] = reply_to
    stored = post_message(client, conversation, "alice", body)
    return (stored.get("meta") or {}).get("agent_recipient") or {}


def _not_the_default(client, conversation: str, agents: list[dict]) -> dict:
    """不是这条对话默认收件人的那位 —— 回复改指它，才看得出改指过。"""
    default = _recipient_of(client, conversation, "开工吧")
    return next(made for made in agents if made["id"] != default.get("instance_id"))


def test_addressing_the_second_teammate_addresses_the_second_teammate(client):
    project = _project(client)
    first = _agent(client, project, "planner", "规划师")
    second = _agent(client, project, "reviewer", "审稿人")
    topic = client.post(
        "/topics",
        json={"project_id": project, "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    for made in (first, second):
        _seat(client, topic, agent_instance_handle(made["id"]))

    # The platform's record of who a message is for is what decides whose turn
    # runs: the turn resolves its agent from this and nothing else.
    async def recipient_of(content: str) -> dict:
        from app.api.deps import get_chat_service
        from app.domain.agent.chat import ChatService

        chat = client.app.dependency_overrides[get_chat_service]()
        assert isinstance(chat, ChatService)
        payloads, *_ = await chat.human_messages.post_user_message(
            uuid.UUID(topic),
            author="alice",
            content=content,
            turn_id=None,
            reply_to=None,
        )
        return (payloads[0].get("meta") or {}).get("agent_recipient") or {}

    to_second = client.portal.call(recipient_of, f"@{second['display_name']} 看一下")
    assert to_second["mentioned"] is True
    assert to_second["handle"] == second["handle"], to_second
    # 记的是哪一个实例，不只是它叫什么。寻址从这里算席位（`reviewer` 这样的名字在
    # 名册上不是任何人的席位，拿它去点名，@ 它起不了一轮）。
    assert to_second["instance_id"] == second["id"], to_second

    to_first = client.portal.call(recipient_of, f"@{first['display_name']} 你来")
    assert to_first["handle"] == first["handle"], to_first


def test_an_at_for_a_teammate_not_in_the_room_is_plain_words(client):
    """@ 一位没坐在这间房里的 AI 队友不是点名：什么都不起，字面也不变成一个
    点得动的名字。坐在房间里的那位照常被点到。"""
    project = _project(client, "One seated, one not")
    seated = _agent(client, project, "planner", "规划师")
    elsewhere = _agent(client, project, "reviewer", "审稿人")
    topic = client.post(
        "/topics",
        json={"project_id": project, "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    _seat(client, topic, agent_instance_handle(seated["id"]))

    async def sent(content: str) -> dict:
        from app.api.deps import get_chat_service
        from app.domain.agent.chat import ChatService

        chat = client.app.dependency_overrides[get_chat_service]()
        assert isinstance(chat, ChatService)
        payloads, *_ = await chat.human_messages.post_user_message(
            uuid.UUID(topic),
            author="alice",
            content=content,
            turn_id=None,
            reply_to=None,
        )
        return payloads[0]

    outside = client.portal.call(sent, f"@{elsewhere['display_name']} 看一下")
    assert outside["content"] == f"@{elsewhere['display_name']} 看一下"
    assert not ((outside.get("meta") or {}).get("agent_recipient") or {}).get(
        "mentioned"
    )

    inside = client.portal.call(sent, f"@{seated['display_name']} 你来")
    assert f"<@{agent_instance_handle(seated['id'])}>" in inside["content"]
    recipient = (inside.get("meta") or {}).get("agent_recipient") or {}
    assert recipient["mentioned"] is True
    assert recipient["handle"] == seated["handle"], recipient


def test_replying_to_a_teammate_hands_the_message_to_that_teammate(client):
    """回谁的消息就是对着谁说的。房间默认哪位不管，收到的该是我回的那位。"""
    project = _project(client, "Reply follows the author")
    first = _agent(client, project, "planner", "规划师")
    second = _agent(client, project, "reviewer", "审稿人")
    room = _room(client, project)
    for made in (first, second):
        _seat(client, room, agent_instance_handle(made["id"]))

    other = _not_the_default(client, room, [first, second])
    asked = _teammate_says(client, project, room, agent_instance_handle(other["id"]))

    recipient = _recipient_of(client, room, "照你说的办", reply_to=asked)
    assert recipient["mentioned"] is True
    assert recipient["instance_id"] == other["id"], recipient
    assert recipient["handle"] == other["handle"], recipient


def test_a_named_teammate_beats_the_reply_target(client):
    """回复里点了别人的名：以点名为准，回复目标不夺它。"""
    project = _project(client, "A name beats the reply target")
    first = _agent(client, project, "planner", "规划师")
    second = _agent(client, project, "reviewer", "审稿人")
    room = _room(client, project)
    for made in (first, second):
        _seat(client, room, agent_instance_handle(made["id"]))

    asked = _teammate_says(client, project, room, agent_instance_handle(second["id"]))

    recipient = _recipient_of(
        client, room, f"@{first['display_name']} 你来看", reply_to=asked
    )
    assert recipient["instance_id"] == first["id"], recipient


def test_replying_to_a_teammate_inside_a_task_hands_it_to_that_teammate(client):
    """任务房间里每条消息都默认点名任务那位，于是「回另一位队友的提问」「点它卡上
    的选项」都落到默认那位身上，提问的那位收不到答案。该落到提问的那位身上。"""
    project = _project(client, "A task beside its owner")
    first = _agent(client, project, "planner", "规划师")
    second = _agent(client, project, "reviewer", "审稿人")
    room = _room(client, project)
    for made in (first, second):
        _seat(client, room, agent_instance_handle(made["id"]))
    task_id = open_task(client, room, owner="alice")["id"]

    other = _not_the_default(client, task_id, [first, second])
    asked = _teammate_says(client, project, task_id, agent_instance_handle(other["id"]))

    recipient = _recipient_of(client, task_id, "按项目口径", reply_to=asked)
    assert recipient["mentioned"] is True
    assert recipient["instance_id"] == other["id"], recipient
