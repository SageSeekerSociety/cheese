"""收藏回答和成员列表不再逐行查作者档案 / 账号。

收藏回答数全部语句：一页 10 行和 50 行一样多。成员列表只数账号表：每行一次的
`get_by_id` 没了，逐人的统计聚合（`build_user_dto`）不在这次范围。
"""

import asyncio
import re
from datetime import UTC, datetime

from sqlalchemy import event

from app.domain.answers.models import Answer, AnswerFavorite
from app.domain.user.models import User, UserProfile
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user


def _count_statements(client):
    """记录一次请求发出的全部语句；返回 (语句列表, 停止函数)。"""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    engine = client.test_app_engine.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    return statements, lambda: event.remove(engine, "before_cursor_execute", record)


def _seed(client, *, people: int) -> int:
    """给 ``favorite-reader`` 建 ``people`` 个带档案的作者，各写一条回答，再全收藏。"""

    async def _seed_rows() -> int:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = UserRepository(session)
            reader = await repo.get_by_username("favorite-reader")
            assert reader is not None
            now = datetime.now(UTC)
            authors = [
                User(
                    username=f"person-{i:03d}",
                    email=f"person-{i:03d}@example.com",
                    created_at=now,
                    updated_at=now,
                )
                for i in range(people)
            ]
            session.add_all(authors)
            await session.flush()
            session.add_all(
                [
                    UserProfile(
                        user_id=author.id,
                        nickname=f"成员 {i:03d}",
                        intro="",
                        avatar_id=1,
                        created_at=now,
                        updated_at=now,
                    )
                    for i, author in enumerate(authors)
                ]
            )
            answers = [
                Answer(
                    question_id=1,
                    created_by_id=author.id,
                    content=f"回答 {i:03d}",
                    created_at=now,
                    updated_at=now,
                )
                for i, author in enumerate(authors)
            ]
            session.add_all(answers)
            await session.flush()
            session.add_all(
                [
                    AnswerFavorite(answer_id=answer.id, user_id=reader.id)
                    for answer in answers
                ]
            )
            await session.commit()
            return reader.id

    return asyncio.run(_seed_rows())


def test_neither_list_reads_its_people_table_once_per_row(client):
    token = seed_user(client, "favorite-reader")
    headers = {"Authorization": f"Bearer {token}"}
    reader_id = _seed(client, people=50)

    def favorite_cost(page_size: int) -> tuple[int, int, list[dict]]:
        statements, stop = _count_statements(client)
        try:
            response = client.get(
                f"/users/{reader_id}/favorites/answers",
                params={"pageSize": page_size},
                headers=headers,
            )
        finally:
            stop()
        assert response.status_code == 200, response.text
        rows = response.json()["data"]["answers"]
        return len(statements), len(rows), rows

    ten, ten_rows, rows10 = favorite_cost(10)
    fifty, fifty_rows, _ = favorite_cost(50)

    assert (ten_rows, fifty_rows) == (10, 50)
    # 每条回答的 sender 仍在：批量取档案没有把作者弄丢。
    assert all(row["sender"] and row["sender"]["nickname"] for row in rows10)
    assert ten == fifty

    def member_cost(page_size: int, q: str | None = None) -> tuple[int, int]:
        params: dict = {"pageSize": page_size}
        if q is not None:
            params["q"] = q
        statements, stop = _count_statements(client)
        try:
            response = client.get("/users", params=params, headers=headers)
        finally:
            stop()
        assert response.status_code == 200, response.text
        users = response.json()["data"]["users"]
        # 只看账号表：`user_profile`、统计这些别的表不在这条判据里。
        user_reads = sum(1 for s in statements if re.search(r'FROM\s+"?user"?\s', s))
        return user_reads, len(users)

    plain_ten, plain_rows_ten = member_cost(10)
    plain_fifty, plain_rows_fifty = member_cost(50)
    assert (plain_rows_ten, plain_rows_fifty) == (10, 50)
    assert plain_ten == plain_fifty == 1

    # 带搜索时原来每条先查一次再在组装时查一次，页大小同样不该决定次数。
    searched_ten, _ = member_cost(10, q="成员")
    searched_fifty, _ = member_cost(50, q="成员")
    assert searched_ten == searched_fifty == 1
