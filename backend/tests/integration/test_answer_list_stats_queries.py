"""答案列表的统计一次问清，且与逐条查的结果逐字段相同。

``GET /questions/{id}/answers`` 一页最多 100 条。改之前每条回答要发 6 次统计
查询 —— 赞/踩、当前浏览者的态度、收藏数、当前浏览者是否收藏、评论数、浏览数
—— 满页就是 600 次往返。本文件钉两件事：

1. 一页的统计只发 1 条 SQL，10 条和 100 条都一样（不随条数增长）；
2. 聚合出来的每个字段与改前逐条查的值一致，连字段顺序也与响应契约一致。

把逐条统计放回 ``list_answers``，两组的计数断言都会红（10 条那页 60 条统计
SQL，100 条那页 600 条）。
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import event

from tests.conftest import seed_user
from tests.integration.conftest import unique_int

#: 一页统计的四个来源。一次问清意味着同一条语句里同时出现这四张表；
#: 逐条问时每个来源各发一条，任何一条都凑不齐四个。按「四张表都在同一条语句
#: 里」来数，与请求同一时刻落在同一个引擎上的后台 SQL 无关。
_STATS_TABLES = (
    "attitude",
    "answer_favorited_by_user",
    "answer_query_log",
    "discussion",
)

#: 种子数据里回答的时间戳，响应里换成毫秒。
CREATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
CREATED_AT_MS = int(CREATED_AT.timestamp() * 1000)

AUTHOR_HANDLE = "stats-author"
VIEWER_HANDLE = "stats-viewer"


@contextmanager
def counting_stats_sql(
    client: TestClient,
) -> Iterator[tuple[list[str], list[str]]]:
    """The stats statements the request issued, in order.

    Yields ``(batched, any_stats)``. ``batched`` holds statements that read
    every source at once — exactly what one page must cost. ``any_stats`` holds
    everything that touched one source, which a per-answer implementation
    floods; it is only used to explain a failure.

    Listens on the engine requests are served from — the same one
    ``test_hot_path_queries`` uses — so setup work on another engine is not
    charged to the request.
    """
    batched: list[str] = []
    any_stats: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        compact = " ".join(statement.split())
        lowered = compact.lower()
        if any(table in lowered for table in _STATS_TABLES):
            any_stats.append(compact)
        if all(table in lowered for table in _STATS_TABLES):
            batched.append(compact)

    engine = client.test_app_engine.sync_engine
    event.listen(engine, "after_cursor_execute", _record)
    try:
        yield batched, any_stats
    finally:
        event.remove(engine, "after_cursor_execute", _record)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_id(token: str) -> int:
    """The numeric user id inside a session token (``sub``)."""

    claims = jwt.decode(token, options={"verify_signature": False})
    return int(claims["sub"])


def _create_question(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(
        "/questions",
        headers=headers,
        json={
            "title": f"Answer stats {unique_int(100000, 999999)}",
            "content": "答案列表的统计",
            "type": 0,
            "topics": [],
            "groupId": None,
            "bounty": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _seed_answers_and_stats(
    client: TestClient,
    *,
    question_id: int,
    author_id: int,
    viewer_id: int,
    first_index: int,
    count: int,
) -> list[int]:
    """Insert answers ``first_index..first_index+count`` and their stats.

    The pattern is deterministic so the test can compute every expected counter
    without asking the database again: answer ``i`` has ``i % 3`` positive votes,
    one negative vote on odd ``i``, ``i % 2`` favourites and comments, and
    ``i % 3`` views. The viewer votes positive on answer 0, negative on answer 1,
    and favourites answer 2. Answer 0 also gets a soft-deleted comment, which
    must not be counted.
    """
    from app.domain.answers.models import Answer, AnswerFavorite, AnswerQueryLog
    from app.domain.discussion.models import Discussion
    from app.domain.questions.models import Attitude

    async def _seed() -> list[int]:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            answers = [
                Answer(
                    question_id=question_id,
                    created_by_id=author_id,
                    content=f"answer {i}",
                    created_at=CREATED_AT,
                    updated_at=CREATED_AT,
                    deleted_at=None,
                )
                for i in range(first_index, first_index + count)
            ]
            session.add_all(answers)
            await session.flush()

            for i, answer in enumerate(answers, start=first_index):
                for j in range(i % 3):
                    session.add(
                        Attitude(
                            attitudable_id=answer.id,
                            attitudable_type="ANSWER",
                            user_id=5000 + i * 10 + j,
                            attitude="POSITIVE",
                            created_at=CREATED_AT,
                            updated_at=CREATED_AT,
                        )
                    )
                if i % 2:
                    session.add(
                        Attitude(
                            attitudable_id=answer.id,
                            attitudable_type="ANSWER",
                            user_id=6000 + i,
                            attitude="NEGATIVE",
                            created_at=CREATED_AT,
                            updated_at=CREATED_AT,
                        )
                    )
                if i == 0:
                    session.add(
                        Attitude(
                            attitudable_id=answer.id,
                            attitudable_type="ANSWER",
                            user_id=viewer_id,
                            attitude="POSITIVE",
                            created_at=CREATED_AT,
                            updated_at=CREATED_AT,
                        )
                    )
                if i == 1:
                    session.add(
                        Attitude(
                            attitudable_id=answer.id,
                            attitudable_type="ANSWER",
                            user_id=viewer_id,
                            attitude="NEGATIVE",
                            created_at=CREATED_AT,
                            updated_at=CREATED_AT,
                        )
                    )

                for j in range(i % 2):
                    session.add(
                        AnswerFavorite(answer_id=answer.id, user_id=7000 + i * 10 + j)
                    )
                if i == 2:
                    session.add(AnswerFavorite(answer_id=answer.id, user_id=viewer_id))

                for j in range(i % 2):
                    session.add(
                        Discussion(
                            model_type="ANSWER",
                            model_id=answer.id,
                            parent_id=None,
                            sender_id=8000 + i * 10 + j,
                            content={"text": f"comment {j}"},
                            created_at=CREATED_AT,
                            updated_at=CREATED_AT,
                            deleted_at=None,
                        )
                    )
                if i == 0:
                    session.add(
                        Discussion(
                            model_type="ANSWER",
                            model_id=answer.id,
                            parent_id=None,
                            sender_id=8000 + i,
                            content={"text": "deleted comment"},
                            created_at=CREATED_AT,
                            updated_at=CREATED_AT,
                            deleted_at=CREATED_AT,
                        )
                    )

                for j in range(i % 3):
                    session.add(
                        AnswerQueryLog(
                            viewer_id=9000 + i * 10 + j,
                            answer_id=answer.id,
                            ip="127.0.0.1",
                            user_agent="pytest",
                            created_at=CREATED_AT,
                        )
                    )

            await session.commit()
            return [answer.id for answer in answers]

    return asyncio.run(_seed())


def _expected_stats(index: int) -> dict:
    positive = index % 3 + (1 if index == 0 else 0)
    negative = (1 if index % 2 else 0) + (1 if index == 1 else 0)
    if index == 0:
        attitude = "POSITIVE"
    elif index == 1:
        attitude = "NEGATIVE"
    else:
        attitude = "UNDEFINED"
    return {
        "positive_count": positive,
        "negative_count": negative,
        "difference": positive - negative,
        "user_attitude": attitude,
        "favorite_count": index % 2 + (1 if index == 2 else 0),
        "is_favorite": index == 2,
        # 第 0 条那条被软删的评论不算。
        "comment_count": index % 2,
        "view_count": index % 3,
    }


def _expected_item(
    *, answer_id: int, question_id: int, index: int, author: dict
) -> dict:
    stats = _expected_stats(index)
    return {
        "id": answer_id,
        "question_id": question_id,
        "content": f"answer {index}",
        "created_by": author["id"],
        "author": author,
        "created_at": CREATED_AT_MS,
        "updated_at": CREATED_AT_MS,
        "attitudes": {
            "positive_count": stats["positive_count"],
            "negative_count": stats["negative_count"],
            "difference": stats["difference"],
            "user_attitude": stats["user_attitude"],
        },
        "favorite_count": stats["favorite_count"],
        "is_favorite": stats["is_favorite"],
        "comment_count": stats["comment_count"],
        "view_count": stats["view_count"],
        "is_group": False,
    }


def test_answer_list_reads_stats_in_one_query_at_any_page_size(client):
    author_token = seed_user(client, AUTHOR_HANDLE)
    author_id = _user_id(author_token)
    viewer_token = seed_user(client, VIEWER_HANDLE)
    viewer_id = _user_id(viewer_token)
    headers = _bearer(viewer_token)

    # 作者资料：响应里的 author 卡片要走同一条资料查询，别让测试绕开它。
    from app.domain.user.models import UserProfile

    async def _give_a_name() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            session.add(
                UserProfile(
                    user_id=author_id,
                    nickname="答案作者",
                    intro="",
                    avatar_id=1,
                    created_at=CREATED_AT,
                    updated_at=CREATED_AT,
                )
            )
            await session.commit()

    asyncio.run(_give_a_name())

    question_id = _create_question(client, headers)
    first_ids = _seed_answers_and_stats(
        client,
        question_id=question_id,
        author_id=author_id,
        viewer_id=viewer_id,
        first_index=0,
        count=10,
    )

    with counting_stats_sql(client) as (small, small_stats):
        response = client.get(
            f"/questions/{question_id}/answers?page_size=10", headers=headers
        )
    assert response.status_code == 200, response.text
    assert len(small) == 1, (
        "a 10-answer page must read all four counter sources in one statement; "
        f"it issued {len(small_stats)} stats statements instead.\n"
        + "\n".join(small_stats[:5])
    )

    items = response.json()["data"]["answers"]
    assert len(items) == 10
    author = {"id": author_id, "nickname": "答案作者", "avatarId": None, "intro": ""}
    index_by_id = {answer_id: i for i, answer_id in enumerate(first_ids)}
    for item in items:
        expected = _expected_item(
            answer_id=item["id"],
            question_id=question_id,
            index=index_by_id[item["id"]],
            author=author,
        )
        # 值逐字段相同，顺序也相同（dict 比较不管顺序，单独钉一次）。
        assert item == expected
        assert list(item) == list(expected)
        assert list(item["attitudes"]) == [
            "positive_count",
            "negative_count",
            "difference",
            "user_attitude",
        ]

    # 再加 90 条，满页 100 条；统计查询数不能变。
    more_ids = _seed_answers_and_stats(
        client,
        question_id=question_id,
        author_id=author_id,
        viewer_id=viewer_id,
        first_index=10,
        count=90,
    )
    index_by_id.update({answer_id: i for i, answer_id in enumerate(more_ids, start=10)})

    with counting_stats_sql(client) as (large, large_stats):
        response = client.get(
            f"/questions/{question_id}/answers?page_size=100", headers=headers
        )
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["answers"]) == 100
    assert len(large) == 1, (
        "a 100-answer page must still read all four counter sources in one "
        f"statement; it issued {len(large_stats)} stats statements instead.\n"
        + "\n".join(large_stats[:5])
    )
    assert len(large) == len(small)

    # 满页里的第 87 条仍是同一条规则算出来的值，不只是条数对。
    items = response.json()["data"]["answers"]
    item = items[87]
    expected = _expected_item(
        answer_id=item["id"],
        question_id=question_id,
        index=index_by_id[item["id"]],
        author=author,
    )
    assert item == expected
