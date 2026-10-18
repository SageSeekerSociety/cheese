"""`review_signals` only asks for the comment threads the list says exist.

`comments_count` is the review's own code-comment count, so a zero means the
thread request can only come back empty. That is the skip this file pins down:
the request count drops and the result does not move a field.

What must NOT be skipped is a review whose summary says nothing but whose
lines do — Forgejo records `COMMENT` with an empty body when somebody submits
line comments without a summary (live example: forgejo/forgejo#14890, review
2118158: state `COMMENT`, empty body, one code comment). The row's body gates
the review's own signal, never its comments.
"""

import httpx
import pytest

from app.domain.review.forgejo_pr import ForgejoClient

BASE = "https://forge.example/api/v1"
PREFIX = "/api/v1/repos/acme/docs/pulls/7"


def review(rid, state, body, *, dismissed=False, comments_count=0, drop_count=False):
    row = {
        "id": rid,
        "state": state,
        "body": body,
        "dismissed": dismissed,
        "comments_count": comments_count,
        "user": {"login": f"user{rid}"},
    }
    if drop_count:
        row.pop("comments_count")
    return row


def comment(cid, *, path="a.py", body=None):
    return {
        "id": cid,
        "user": {"login": f"user{cid}"},
        "body": body or f"正文{cid}",
        "path": path,
    }


def client(reviews, threads, fetched):
    """A recording transport: `fetched` collects the review ids we asked about."""

    def server(request):
        path = request.url.path
        if path == PREFIX + "/reviews":
            return httpx.Response(200, json=reviews)
        if path.startswith(PREFIX + "/reviews/"):
            rid = int(path[len(PREFIX + "/reviews/") :].split("/")[0])
            fetched.append(rid)
            return httpx.Response(200, json=threads.get(rid, []))
        if path == "/api/v1/repos/acme/docs/issues/7/comments":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request: {path}")

    return ForgejoClient(BASE, transport=httpx.MockTransport(server))


async def signals_of(reviews, threads):
    fetched = []
    signals = await client(reviews, threads, fetched).review_signals(
        owner="acme", repo="docs", number=7, token="project-token"
    )
    return fetched, [(s.id, s.kind, s.author, s.body, s.where) for s in signals]


@pytest.mark.anyio
async def test_the_count_zero_reviews_are_the_ones_skipped():
    reviews = [
        # 1：有正文、有行内评论 —— 要问。
        review(1, "REQUEST_CHANGES", "请改这里", comments_count=1),
        # 2：有正文、没有行内评论 —— 唯一被跳过的一条。
        review(2, "COMMENT", "看起来不错"),
        # 3：空正文但有行内评论 —— 那正是「只写行内评论、没写总结」的评审。
        review(3, "COMMENT", "", comments_count=1),
        # 4：状态不在动作范围（批准）—— 它自己的信号不进结果，行内评论照旧。
        review(4, "APPROVED", "", comments_count=1),
        # 5：已撤回 —— 同上，行内评论照旧。
        review(5, "COMMENT", "", dismissed=True, comments_count=2),
    ]
    threads = {
        1: [comment(101)],
        2: [],
        3: [comment(301, path="backend/app.py")],
        4: [comment(401)],
        5: [comment(501), comment(502)],
    }

    fetched, got = await signals_of(reviews, threads)

    assert fetched == [1, 3, 4, 5]
    assert got == [
        ("review:1", "changes_requested", "user1", "请改这里", ""),
        ("comment:101", "comment", "user101", "正文101", "a.py"),
        ("review:2", "commented", "user2", "看起来不错", ""),
        ("comment:301", "comment", "user301", "正文301", "backend/app.py"),
        ("comment:401", "comment", "user401", "正文401", "a.py"),
        ("comment:501", "comment", "user501", "正文501", "a.py"),
        ("comment:502", "comment", "user502", "正文502", "a.py"),
    ]


@pytest.mark.anyio
async def test_a_missing_comment_count_is_read_the_old_way():
    """对面不报这个字段就照旧去取：少一次请求不值得用丢意见来换。"""
    reviews = [review(11, "COMMENT", "有话说", drop_count=True)]
    threads = {11: [comment(1101)]}

    fetched, got = await signals_of(reviews, threads)

    assert fetched == [11]
    assert got[1][0] == "comment:1101"


@pytest.mark.anyio
async def test_the_requests_line_up_with_the_signals_they_produce():
    """每一条被问过的评论都要变成一条信号，没有例外（跳过的才允许没有）。"""
    reviews = [
        review(1, "COMMENT", "有话说", comments_count=2),
        review(2, "COMMENT", "也有", comments_count=1),
        review(3, "COMMENT", "没有评论"),
    ]
    threads = {1: [comment(101), comment(102)], 2: [comment(201)]}

    fetched, got = await signals_of(reviews, threads)

    assert fetched == [1, 2]
    assert sum(1 for s in got if s[1] == "comment") == len(threads[1]) + len(threads[2])
    assert [s[0] for s in got if s[1] == "comment"] == [
        "comment:101",
        "comment:102",
        "comment:201",
    ]


@pytest.mark.anyio
async def test_with_comments_false_never_asks():
    fetched = []
    signals = await client(
        [review(1, "COMMENT", "有话说", comments_count=3)], {}, fetched
    ).review_signals(
        owner="acme", repo="docs", number=7, token="project-token", with_comments=False
    )

    assert fetched == []
    assert [s.id for s in signals] == ["review:1"]
