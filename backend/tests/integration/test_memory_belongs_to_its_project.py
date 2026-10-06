"""The memory page is a project's own: who may read it, and who may prune it.

``GET /memory`` and ``DELETE /memory/{id}`` once took no credential at all, so
anyone who could reach the API listed any project's memory — including what its
agents had noted about individual people — and deleted entries by id. These
tests pin the rules that replaced that:

- nobody signed in reads or prunes anything;
- the project's shared memory is read by the people who may read the project;
- a person's own memory is read by that person alone;
- a memory is pruned by someone the listing would show it to, and a refused
  prune answers exactly what an unknown id answers.

The memory itself is the file tree (``memory_files``, see
``docs/manual/dev/memory.md``): a project has one shared scope (``team``) and
每个项目成员有一份自己的（``private/<handle>``）。写入方（agent 的文件工具、
以及 `cheese_remember` 的替代者）在会话机上，所以这里直接用平台自己的写入口
（``MemoryFileStore``）种树——旧的条目池已经没有写入方，种不出东西来。
"""

import asyncio
import uuid

from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFile,
    MemoryFileScope,
    MemoryType,
)
from app.domain.memory.files_store import MemoryFileStore
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)

# The harness sends the global sandbox token on every request; a caller in
# these tests presents only what it names.
NO_CREDENTIAL = {"X-Cheese-Token": ""}


def _as(handle: str) -> dict[str, str]:
    return {**NO_CREDENTIAL, **session_auth_headers(handle)}


def _remember(
    client, project_id: str, scope: MemoryFileScope, owner: str | None, memories: dict
) -> None:
    """Write ``path → body`` into that scope, index line included.

    索引也要写：删一条记忆要连索引里那一行一起删，而「一起删了没有」是这些用例
    看得见的事（`MemoryFileStore.forget`）。
    """

    async def _write() -> None:
        async with client.test_factory() as s:
            store = MemoryFileStore(s)
            project = uuid.UUID(project_id)
            lines = []
            for path, body in memories.items():
                memory = MemoryFile(
                    name=path[: -len(".md")],
                    description="一条记忆",
                    type=MemoryType.project,
                    body=body,
                )
                await store.write(
                    project_id=project,
                    scope=scope,
                    owner_handle=owner,
                    path=path,
                    content=memory.text(),
                    updated_by="cheese",
                    expected_version=None,
                )
                lines.append(memory.index_line(path))
            await store.write(
                project_id=project,
                scope=scope,
                owner_handle=owner,
                path=INDEX_NAME,
                content="\n".join(lines) + "\n",
                updated_by="cheese",
                expected_version=None,
            )
            await s.commit()

    asyncio.run(_write())


def _remember_team(client, project_id: str, memories: dict) -> None:
    _remember(client, project_id, MemoryFileScope.team, None, memories)


def _remember_private(client, project_id: str, person: str, memories: dict) -> None:
    _remember(client, project_id, MemoryFileScope.private, person, memories)


def _index_lines(client, project_id: str, scope: MemoryFileScope, owner: str | None):
    """那个作用域的索引现在长什么样——删一条之后要读的那个东西。"""

    async def _read() -> str:
        async with client.test_factory() as s:
            return (
                await MemoryFileStore(s).index_text(uuid.UUID(project_id), scope, owner)
                or ""
            )

    return asyncio.run(_read())


def _list(client, project_id: str, headers: dict, **params):
    return client.get(
        "/memory", params={"project_id": project_id, **params}, headers=headers
    )


def _contents(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {e["content"] for e in response.json()["data"]["data"]}


def _id_of(client, project_id: str, handle: str, content: str, **params) -> str:
    rows = _list(client, project_id, _as(handle), **params).json()["data"]["data"]
    return next(e["id"] for e in rows if e["content"] == content)


def _project_with_memory(client) -> str:
    """alice owns it, bob is on its project; the tree holds one thing the project
    shares and one thing each of them keeps for themselves."""
    project_id = new_project(client, owner="alice")["id"]
    join_project_team(client, project_id, "bob")
    _remember_team(
        client, project_id, {"deploy-steps.md": "部署脚本在 deploy/deploy.sh"}
    )
    _remember_private(
        client, project_id, "alice", {"answer-first.md": "alice 要结论在最前面"}
    )
    _remember_private(client, project_id, "bob", {"weekend.md": "bob 周末不看消息"})
    return project_id


def test_nobody_signed_in_reads_or_prunes_anything(client):
    project_id = _project_with_memory(client)
    entry = _id_of(client, project_id, "alice", "部署脚本在 deploy/deploy.sh")

    assert _list(client, project_id, NO_CREDENTIAL).status_code == 401
    assert (
        _list(client, project_id, NO_CREDENTIAL, user_handle="alice").status_code == 401
    )
    assert client.delete(f"/memory/{entry}", headers=NO_CREDENTIAL).status_code == 401
    # Answered before the id is looked at, so it says nothing about the id.
    unknown = client.delete(f"/memory/{uuid.uuid4()}", headers=NO_CREDENTIAL)
    assert unknown.status_code == 401
    assert "部署脚本在 deploy/deploy.sh" in _contents(
        _list(client, project_id, _as("alice"))
    )


def test_an_outsider_reads_nothing_of_another_projects_memory(client):
    project_id = _project_with_memory(client)

    assert _list(client, project_id, _as("mallory")).status_code == 403
    assert (
        _list(client, project_id, _as("mallory"), user_handle="alice").status_code
        == 403
    )


def test_an_outsiders_prune_is_answered_like_an_unknown_id(client):
    project_id = _project_with_memory(client)
    entry = _id_of(client, project_id, "alice", "部署脚本在 deploy/deploy.sh")

    refused = client.delete(f"/memory/{entry}", headers=_as("mallory"))
    unknown = client.delete(f"/memory/{uuid.uuid4()}", headers=_as("mallory"))

    assert refused.status_code == unknown.status_code == 404
    assert refused.json() == unknown.json()
    assert "部署脚本在 deploy/deploy.sh" in _contents(
        _list(client, project_id, _as("alice"))
    )


def test_a_member_reads_and_prunes_the_projects_memory(client):
    project_id = _project_with_memory(client)

    # 项目共享的那一份是项目内容，队友读得到；两个作用域各有各的名字，页面靠它贴标签。
    listed = _list(client, project_id, _as("bob"))
    assert _contents(listed) == {"部署脚本在 deploy/deploy.sh"}
    assert [e["scope"] for e in listed.json()["data"]["data"]] == [
        MemoryFileScope.team.value
    ]
    entry = _id_of(client, project_id, "bob", "部署脚本在 deploy/deploy.sh")
    assert client.delete(f"/memory/{entry}", headers=_as("bob")).status_code == 200
    assert _contents(_list(client, project_id, _as("alice"))) == set()


def test_a_person_reads_the_memory_of_their_own_scope(client):
    project_id = _project_with_memory(client)

    assert _contents(_list(client, project_id, _as("alice"), user_handle="alice")) == {
        "部署脚本在 deploy/deploy.sh",
        "alice 要结论在最前面",
    }
    # bob is in the project, and still not the person that scope belongs to.
    assert _list(client, project_id, _as("bob"), user_handle="alice").status_code == 403


def test_a_note_of_someones_own_is_pruned_by_that_person_alone(client):
    project_id = _project_with_memory(client)
    own = _id_of(
        client, project_id, "alice", "alice 要结论在最前面", user_handle="alice"
    )

    refused = client.delete(f"/memory/{own}", headers=_as("bob"))
    unknown = client.delete(f"/memory/{uuid.uuid4()}", headers=_as("bob"))
    assert refused.status_code == unknown.status_code == 404
    assert refused.json() == unknown.json()

    assert client.delete(f"/memory/{own}", headers=_as("alice")).status_code == 200
    assert "alice 要结论在最前面" not in _contents(
        _list(client, project_id, _as("alice"), user_handle="alice")
    )


def test_pruning_a_memory_takes_its_index_line_with_it(client):
    """剪一条记忆剪的是「文件 + 索引里那一行」。

    只删文件的话，下一轮注入的索引里还挂着一条指向不存在文件的指针——读起来像
    「这条记忆在」。所以这一页用 `MemoryFileStore.forget`，这条用例盯的就是那一行。
    """
    project_id = _project_with_memory(client)
    assert "deploy-steps.md" in _index_lines(
        client, project_id, MemoryFileScope.team, None
    )

    entry = _id_of(client, project_id, "bob", "部署脚本在 deploy/deploy.sh")
    assert client.delete(f"/memory/{entry}", headers=_as("bob")).status_code == 200

    index = _index_lines(client, project_id, MemoryFileScope.team, None)
    assert "deploy-steps.md" not in index


def test_the_index_is_not_a_memory(client):
    """`MEMORY.md` 是「有哪些条」的目录，不是一条记忆。"""
    project_id = _project_with_memory(client)

    rows = _list(client, project_id, _as("alice"), user_handle="alice").json()["data"][
        "data"
    ]
    assert {e["path"] for e in rows} == {"deploy-steps.md", "answer-first.md"}


def _project_credential(client, project_id: str, steward: str) -> str:
    """The project's agent credential, with the agent granted a member row — a
    credential by itself grants no role (see test_project_agent_credential)."""
    owner = {**NO_CREDENTIAL, "Authorization": f"Bearer {seed_user(client, steward)}"}
    issued = client.post(
        f"/projects/{project_id}/agent-credential", json={}, headers=owner
    )
    assert issued.status_code == 200, issued.text
    added = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": issued.json()["data"]["agent_handle"]},
        headers=owner,
    )
    assert added.status_code == 200, added.text
    return issued.json()["data"]["token"]


def test_the_projects_agent_reads_its_own_projects_memory_and_no_other(client):
    project_id = _project_with_memory(client)
    other_id = new_project(client, name="Q", owner="carol")["id"]
    _remember_team(client, other_id, {"q.md": "Q 的事"})
    token = _project_credential(client, project_id, "alice")

    listed = _list(client, project_id, {"X-Cheese-Token": token})
    assert _contents(listed) == {"部署脚本在 deploy/deploy.sh"}
    assert {"updated_at", "path"} <= set(listed.json()["data"]["data"][0])
    assert _list(client, other_id, {"X-Cheese-Token": token}).status_code == 403
