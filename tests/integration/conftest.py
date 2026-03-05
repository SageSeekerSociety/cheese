import os
import random
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime

import bcrypt
import httpx
import psycopg2
import pytest

from app.core.config import settings


def _get_psycopg2_dsn() -> str:
    db_url = settings.database_url
    if db_url.startswith("postgresql+psycopg2://"):
        return db_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    elif db_url.startswith("postgresql+asyncpg://"):
        return db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return db_url


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


@dataclass
class CreatedUser:
    user_id: int
    username: str
    password: str
    email: str
    nickname: str
    avatar_id: int
    intro: str
    token: str | None = None


class UserCreator:
    def __init__(self):
        self._suffix = random.randint(10000000, 99999999)

    def _test_username(self) -> str:
        suffix = random.randint(10000000000, 99999999999)
        return f"PyTestUser-{suffix}"

    def _test_password(self) -> str:
        return "abc123456Test"

    def _test_email(self) -> str:
        suffix = random.randint(10000000000, 99999999999)
        return f"pytest-{suffix}@ruc.edu.cn"

    def _test_nickname(self) -> str:
        return "pytest_user"

    def _test_avatar_id(self) -> int:
        return 1

    def _test_intro(self) -> str:
        return "This user has not set an introduction yet."

    def create_user(
        self,
        username: str | None = None,
        password: str | None = None,
        email: str | None = None,
        nickname: str | None = None,
        avatar_id: int | None = None,
        intro: str | None = None,
    ) -> CreatedUser:
        username = username or self._test_username()
        password = password or self._test_password()
        email = email or self._test_email()
        nickname = nickname or self._test_nickname()
        avatar_id = avatar_id or self._test_avatar_id()
        intro = intro or self._test_intro()

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        now = datetime.now(UTC)

        dsn = _get_psycopg2_dsn()
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO "user" (username, email, hashed_password, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (username, email, hashed, now, now),
                )
                user_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO user_profile (user_id, nickname, intro, avatar_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, nickname, intro, avatar_id, now, now),
                )
            conn.commit()
        finally:
            conn.close()

        return CreatedUser(
            user_id=user_id,
            username=username,
            password=password,
            email=email,
            nickname=nickname,
            avatar_id=avatar_id,
            intro=intro,
        )

    def login(self, client: httpx.Client, username: str, password: str) -> str:
        response = client.post(
            "/users/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        return data["data"]["accessToken"]


@pytest.fixture(scope="session")
def test_server_port() -> Generator[int, None, None]:
    port = _find_free_port()
    env = os.environ.copy()
    env["DATABASE_URL"] = settings.database_url
    env["REDIS_URL"] = settings.redis_url

    log_file = open(f"/tmp/test_server_{port}.log", "w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "debug",
        ],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_server(port):
        process.terminate()
        process.wait(timeout=5)
        log_file.close()
        with open(f"/tmp/test_server_{port}.log") as f:
            pytest.fail(f"Test server failed to start.\nLog:\n{f.read()}")

    yield port

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    log_file.close()
    print(f"\nServer log available at /tmp/test_server_{port}.log")


@pytest.fixture
def user_client() -> UserCreator:
    return UserCreator()


@pytest.fixture
def api_client(test_server_port: int) -> Generator[httpx.Client, None, None]:
    with httpx.Client(base_url=f"http://127.0.0.1:{test_server_port}", timeout=30.0) as client:
        yield client


@pytest.fixture
def authenticated_user(user_client: UserCreator, api_client: httpx.Client) -> CreatedUser:
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    user.token = token
    return user


@pytest.fixture
def auth_headers(authenticated_user: CreatedUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {authenticated_user.token}"}
