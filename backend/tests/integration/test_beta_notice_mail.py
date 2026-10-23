"""The 「知是正在内测」 letter: who it goes to, what each person's copy says, and
that the sender mails nobody on a dry run and nobody twice.

Pinned here because the script is run by hand against the real user table: a
wrong recipient rule mails agents or dead addresses, and a lost checkpoint mails
everyone again.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

import scripts.send_beta_notice as send_beta_notice
from app.domain.identity.models import AgentBinding
from app.domain.user.models import User, UserSession
from tests.conftest import seed_user


def _bearer(client, handle: str) -> dict:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _ids(client, *handles: str) -> dict[str, int]:
    async def read() -> dict[str, int]:
        async with client.test_factory() as session:
            rows = await session.execute(
                select(User.username, User.id).where(User.username.in_(handles))
            )
            return dict(rows.all())

    return asyncio.run(read())


class FakeSender:
    def __init__(self) -> None:
        self.letters: list[dict] = []

    async def send(self, *, to, subject, body_html, body_text=None) -> bool:
        self.letters.append(
            {"to": to, "subject": subject, "html": body_html, "text": body_text}
        )
        return True


@pytest.fixture
def people(client):
    """A person who has claimed the 内测 credits, one who has not, an agent
    identity, an account with a placeholder address, a seeded test account under
    a reserved name, and an account carried over from the old platform that has
    never signed in here. All but the last have signed in."""
    claimed = _bearer(client, "bn-claimed")
    _bearer(client, "bn-fresh")
    _bearer(client, "bn-agent")
    _bearer(client, "bn-placeholder")
    _bearer(client, "bn-seeded")
    _bearer(client, "bn-dormant")
    assert client.post("/users/me/credits/beta", headers=claimed).is_success
    handles = (
        "bn-claimed",
        "bn-fresh",
        "bn-agent",
        "bn-placeholder",
        "bn-seeded",
        "bn-dormant",
    )
    ids = _ids(client, *handles)

    async def mark() -> None:
        async with client.test_factory() as session:
            session.add(AgentBinding(user_id=ids["bn-agent"]))
            now = datetime.now(UTC)
            for handle in handles[:-1]:
                session.add(
                    UserSession(
                        user_id=ids[handle],
                        current_hash=f"{handle}-session".ljust(64, "0"),
                        login_method="password",
                        user_agent="test",
                        ip="127.0.0.1",
                        last_used_at=now,
                        expires_at=now + timedelta(days=30),
                    )
                )
            # seed_user gives example.com, a name reserved for examples that the
            # letter skips; the real people get an ordinary address.
            for handle in ("bn-claimed", "bn-fresh", "bn-dormant"):
                await session.execute(
                    update(User)
                    .where(User.id == ids[handle])
                    .values(email=f"{handle}@school.edu.cn")
                )
            await session.execute(
                update(User)
                .where(User.id == ids["bn-placeholder"])
                .values(email="bn-placeholder@placeholder.internal")
            )
            await session.execute(
                update(User)
                .where(User.id == ids["bn-seeded"])
                .values(email="bn-seeded@agents.cheese.local")
            )
            await session.commit()

    asyncio.run(mark())
    return ids


@pytest.fixture
def run(client, monkeypatch, tmp_path):
    sender = FakeSender()
    monkeypatch.setattr(
        send_beta_notice, "async_session_factory", client.test_request_factory
    )
    monkeypatch.setattr(send_beta_notice, "get_email_sender", lambda: sender)

    def go(**kwargs) -> int:
        return client.portal.call(lambda: send_beta_notice.main(tmp_path, **kwargs))

    go.sender = sender
    go.state = tmp_path
    return go


def _mine(letters: list[dict], *handles: str) -> dict[str, dict]:
    return {
        letter["to"].split("@")[0]: letter
        for letter in letters
        if letter["to"].split("@")[0] in handles
    }


def test_a_dry_run_lists_the_people_and_mails_nobody(people, run):
    assert run(send=False, sample="bn-fresh") == 0
    assert run.sender.letters == []
    listed = {int(line) for line in (run.state / "recipients.txt").read_text().split()}
    assert {people["bn-claimed"], people["bn-fresh"]} <= listed
    assert people["bn-agent"] not in listed
    assert people["bn-placeholder"] not in listed
    assert people["bn-seeded"] not in listed
    assert people["bn-dormant"] not in listed
    sample = (run.state / f"sample-{people['bn-fresh']}.txt").read_text()
    assert "Subject: 知是正在内测" in sample
    assert not list((run.state / "sent").iterdir())


def test_each_person_is_mailed_once_and_their_copy_says_whether_they_claimed(
    people, run
):
    assert run(send=True, sample=None) == 0
    mine = _mine(
        run.sender.letters,
        "bn-claimed",
        "bn-fresh",
        "bn-agent",
        "bn-placeholder",
        "bn-dormant",
    )
    assert set(mine) == {"bn-claimed", "bn-fresh"}
    assert "你已经领取过了" in mine["bn-claimed"]["text"]
    assert "打开额度页" in mine["bn-claimed"]["text"]
    assert "你还没领取" in mine["bn-fresh"]["text"]
    assert "点的额度（每月 1 日重置），这段时间里" in mine["bn-fresh"]["text"]
    assert "· 每月方案额度" not in mine["bn-fresh"]["text"]
    assert "去领取 500 点" in mine["bn-fresh"]["text"]
    assert "/teams/bn-fresh/credits" in mine["bn-fresh"]["html"]

    before = len(run.sender.letters)
    assert run(send=True, sample=None) == 0
    assert len(run.sender.letters) == before
    log = (run.state / "run.log").read_text()
    assert f"user={people['bn-fresh']} status=skipped reason=already_sent" in log
