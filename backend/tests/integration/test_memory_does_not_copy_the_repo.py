"""`cheese_remember` 不收 repo 里已经写着的事实（结论 61 后半）。

`docs` 和 `code` 是 repo 自己的事，memory 只记 repo 里查不到的。判据落在写入端而不
是提示词里：提示词只在模型愿意照做时成立，而要挡的正是它没照做的那几次。

这里的检出目录是个替身，但它遵守真实协议——还回的就是执行器 `repo_search` 那一份
``{"path", "line", "text"}``（结论 59）。真机器上跑的是 `git grep`，那部分不是这条
用例要证的东西。
"""

from app.domain.memory import redundant
from tests.integration.conftest import post_project


def _project(client) -> str:
    return post_project(client, json={"name": "Repo memory"}).json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    return client.post(
        "/topics", json={"project_id": project_id, "title": "干活"}
    ).json()["data"]["id"]


def _checkout_holding(monkeypatch, *lines: dict) -> None:
    async def search(terms: list[str]) -> list[dict]:
        return [
            line
            for line in lines
            if any(term.lower() in str(line["text"]).lower() for term in terms)
        ]

    monkeypatch.setattr(
        redundant,
        "agent_checkout_search",
        lambda db, room, agent_handle, harness: search,
    )


def test_a_fact_the_repo_already_carries_is_refused_and_the_file_is_named(
    client, monkeypatch
):
    pid = _project(client)
    tid = _topic(client, pid)
    _checkout_holding(
        monkeypatch,
        {
            "path": "docs/frontend.md",
            "line": 12,
            "text": "前端构建用 pnpm，不要用 npm。",
        },
    )

    r = client.post(
        f"/projects/{pid}/memory",
        json={"content": "前端构建用 pnpm，不要用 npm", "topic": tid},
    )
    assert r.status_code == 422
    # 拒绝理由里没有那个文件，调用方能做的只有换个说法再写一遍。
    assert "docs/frontend.md" in r.json()["message"]

    assert client.get(f"/memory?project_id={pid}").json()["data"]["data"] == []


def test_a_fact_the_repo_does_not_carry_is_stored(client, monkeypatch):
    """前提：同一条路径在没命中的时候照常存——否则上面那条证明不了是检索起的作用。"""
    pid = _project(client)
    tid = _topic(client, pid)
    _checkout_holding(
        monkeypatch,
        {"path": "README.md", "line": 3, "text": "CI 在 GitHub Actions 上跑"},
    )

    r = client.post(
        f"/projects/{pid}/memory",
        json={"content": "王老师周三下午不看消息，有事提前一天问", "topic": tid},
    )
    assert r.status_code == 200
    listed = client.get(f"/memory?project_id={pid}").json()["data"]["data"]
    stored = [e["content"] for e in listed]
    assert stored == ["王老师周三下午不看消息，有事提前一天问"]
