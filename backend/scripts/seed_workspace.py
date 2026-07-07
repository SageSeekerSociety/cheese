"""Seed test users for the 知是 2.0 workspace (Phase A).

Creates a few password-login humans plus a couple of reserved "agent" users
(agents are just users — see docs/design/architecture.md). Idempotent: skips a
user whose username already exists.

Run from backend/:  uv run python scripts/seed_workspace.py
"""

import asyncio

from app.db.session import AsyncSessionLocal
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import UserAuthService

PASSWORD = "Cheese@2026"

# (username, nickname, email, kind)
SEED_USERS = [
    ("alice", "Alice", "alice@cheese.test", "human"),
    ("bob", "Bob", "bob@cheese.test", "human"),
    ("carol", "Carol", "carol@cheese.test", "human"),
    ("cheese-main", "芝士·本体", "cheese-main@cheese.test", "agent"),
    ("cheese-data", "芝士·数据分身", "cheese-data@cheese.test", "agent"),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        auth = UserAuthService(
            UserRepository(session),
            UserProfileRepository(session),
            UserFollowingRepository(session),
            UserStatisticsRepository(session),
        )
        created, skipped = [], []
        for username, nickname, email, kind in SEED_USERS:
            if await auth._user_repo.is_username_taken(username):
                skipped.append(username)
                continue
            user, _ = await auth.register_with_password(
                username=username, nickname=nickname, email=email, password=PASSWORD
            )
            created.append((username, user.id, kind))
        await session.commit()

    print(f"password for all seeded users: {PASSWORD}")
    if created:
        print("created:")
        for username, uid, kind in created:
            print(f"  #{uid:<4} {username:<14} ({kind})")
    if skipped:
        print(f"skipped (already exist): {', '.join(skipped)}")


if __name__ == "__main__":
    asyncio.run(main())
