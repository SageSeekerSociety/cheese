"""Memory search endpoint (`cheese recall`) on the flat DB backend.

The behaviour under test is the one that decides whether memory is usable at
all: a fact can only help if it can be found by someone who does not already
know the words it was written with.
"""

from app.core.sandbox_auth import mint_scoped_token


def _project(client) -> str:
    r = client.post("/api/projects", json={"name": "Mem"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    return client.post(
        "/api/topics", json={"project_id": project_id, "title": "memory scope"}
    ).json()["data"]["id"]


def test_search_project_memory(client):
    pid = _project(client)
    for fact in ("技术栈=FastAPI", "数据库用 PostgreSQL", "前端 Vue 3"):
        r = client.post(f"/api/projects/{pid}/memory", json={"content": fact})
        assert r.status_code == 200

    r = client.post(f"/api/projects/{pid}/memory/search", json={"query": "PostgreSQL"})
    assert r.status_code == 200
    hits = r.json()["data"]["hits"]
    assert len(hits) == 1
    assert "PostgreSQL" in hits[0]["abstract"]
    assert hits[0]["uri"].startswith("db://memory/")


def test_search_personal_memory(client):
    pid = _project(client)
    r = client.post(
        f"/api/projects/{pid}/memory",
        json={"content": "偏好简洁汇报", "scope": "user", "owner": "andyl"},
    )
    assert r.status_code == 200

    r = client.post(
        f"/api/projects/{pid}/memory/search",
        json={"query": "简洁", "scope": "user", "owner": "andyl"},
    )
    assert r.status_code == 200
    assert len(r.json()["data"]["hits"]) == 1

    # Project scope must not see the personal fact.
    r = client.post(f"/api/projects/{pid}/memory/search", json={"query": "简洁"})
    assert r.json()["data"]["hits"] == []


def test_a_plain_question_finds_the_fact_that_answers_it(client):
    """The whole point: recall by how the question is asked, not by which words
    the fact happened to be written with."""
    pid = _project(client)
    for fact in (
        "CI 失败要看日志：cheese gh-token 铸只读 token，再 gh run view --log-failed",
        "前端构建用 pnpm，不要用 npm",
        "沙箱里没有 docker，用 .claude/scripts/dev-db.sh 起测试库",
    ):
        client.post(f"/api/projects/{pid}/memory", json={"content": fact})

    r = client.post(
        f"/api/projects/{pid}/memory/search", json={"query": "CI 失败日志怎么看"}
    )
    assert r.status_code == 200
    hits = r.json()["data"]["hits"]
    assert hits, "人话查询一条都没命中"
    assert "gh-token" in hits[0]["abstract"], "最相关的那条没排在第一"


def test_a_question_about_the_sandbox_finds_the_sandbox_fact(client):
    pid = _project(client)
    for fact in (
        "沙箱里没有 docker，用 .claude/scripts/dev-db.sh 起 Postgres 和 Redis",
        "提交信息用英文，聚焦 why",
    ):
        client.post(f"/api/projects/{pid}/memory", json={"content": fact})

    hits = client.post(
        f"/api/projects/{pid}/memory/search",
        json={"query": "没有 docker 的环境里测试怎么跑起来"},
    ).json()["data"]["hits"]
    assert hits
    assert "dev-db.sh" in hits[0]["abstract"]


def test_hits_come_back_ranked_by_how_much_of_the_query_they_cover(client):
    pid = _project(client)
    client.post(
        f"/api/projects/{pid}/memory",
        json={"content": "迁移合并冲突要先看 alembic heads"},
    )
    client.post(f"/api/projects/{pid}/memory", json={"content": "alembic 用来做迁移"})

    hits = client.post(
        f"/api/projects/{pid}/memory/search", json={"query": "alembic 迁移冲突"}
    ).json()["data"]["hits"]
    assert len(hits) == 2
    assert hits[0]["score"] > hits[1]["score"]
    assert "heads" in hits[0]["abstract"]


def test_an_unrelated_question_still_finds_nothing(client):
    """Broader matching must not turn recall into 'everything matches'."""
    pid = _project(client)
    client.post(f"/api/projects/{pid}/memory", json={"content": "前端构建用 pnpm"})

    hits = client.post(
        f"/api/projects/{pid}/memory/search", json={"query": "报销流程找谁审批"}
    ).json()["data"]["hits"]
    assert hits == []


def test_wildcards_in_a_query_are_literal(client):
    pid = _project(client)
    client.post(f"/api/projects/{pid}/memory", json={"content": "部署脚本在 deploy.sh"})

    hits = client.post(
        f"/api/projects/{pid}/memory/search", json={"query": "100%"}
    ).json()["data"]["hits"]
    assert hits == []


def test_search_requires_query(client):
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/memory/search", json={"query": "  "})
    assert r.status_code == 422


def test_memory_search_requires_a_credential(client):
    pid = _project(client)
    r = client.post(
        f"/api/projects/{pid}/memory/search",
        json={"query": "x"},
        headers={"X-Cheese-Token": ""},
    )
    assert r.status_code == 401


def test_memory_body_topic_must_match_the_scoped_token(client):
    pid = _project(client)
    mine = _topic(client, pid)
    other = _topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=mine)

    for suffix, body in (
        ("memory", {"content": "cross-topic write", "topic": other}),
        ("memory/search", {"query": "cross-topic", "topic": other}),
    ):
        r = client.post(
            f"/api/projects/{pid}/{suffix}",
            json=body,
            headers={"X-Cheese-Token": token},
        )
        assert r.status_code == 403


def test_memory_body_topic_must_belong_to_the_url_project(client):
    pid = _project(client)
    foreign_pid = _project(client)
    foreign_topic = _topic(client, foreign_pid)
    r = client.post(
        f"/api/projects/{pid}/memory",
        json={"content": "wrong project", "topic": foreign_topic},
    )
    assert r.status_code == 403
