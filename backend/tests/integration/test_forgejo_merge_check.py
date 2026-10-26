"""On Forgejo, a PR is a conflict when git's test merge says so, and only then.

Forgejo answers `mergeable: false` while its merge check is still running as
well as on a conflict, and the check can run for a long time. The rules, as
stated before the code:

- a PR whose head merges cleanly into its base is never a conflict, however
  long Forgejo keeps saying false: no conflict on the card, no conflict notice
  to the agent, and accepting it does not say it conflicts;
- a PR whose head and base changed the same line is a conflict once the test
  merge has run: the card turns dirty and the agent is told.
"""

import asyncio
import time
import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app.core.config import settings
from app.domain.review.forgejo_pr import ForgejoClient
from tests.conftest import wait_work_idle
from tests.integration.test_accept_pr import _accept, _cards, _poll, _ready_card
from tests.integration.test_accept_pr import app_world as _app_world_fixture
from tests.integration.test_nudge_pr_signals import _nudges
from tests.support.git_origin import proposal

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _forgejo_refusing(fake, monkeypatch, tmp_path, *, conflicting: bool):
    """The card's PR as Forgejo serves it, through the real Forgejo client:
    `mergeable: false` on every reading, over a repository git can fetch."""
    pr = proposal(tmp_path / "repo", conflicting=conflicting)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    monkeypatch.setattr(
        ForgejoClient, "git_url", lambda self, owner, repo: pr.url, raising=False
    )

    def serve(request):
        number = int(request.url.path.rsplit("/", 1)[1])
        row = fake.prs[number]
        return httpx.Response(
            200,
            json={
                "number": number,
                "title": row.get("title") or "",
                "head": {"ref": row["head"], "sha": row["head_sha"]},
                "base": {"ref": row["base"], "sha": pr.base},
                "state": row["state"],
                "merged": row["merged"],
                "mergeable": False,
            },
        )

    forgejo = ForgejoClient(
        "https://forge.example/api/v1", transport=httpx.MockTransport(serve)
    )
    fake.pull_request_status = forgejo.pull_request_status
    return pr


def _git_has_answered(pr) -> None:
    """Wait for the test merge the first reading started in the background."""
    # deferred-import: the module the rule is about; an older tree without it
    # must fail on the rule's own assertion before getting here.
    from app.domain.review import merge_check

    deadline = time.monotonic() + 30
    while merge_check.answer(pr.url, pr.base, pr.head) is None:
        assert time.monotonic() < deadline, "the test merge never answered"
        time.sleep(0.05)


def _time_passes(client, card_id: str, minutes: int) -> None:
    """Every moment the card has recorded moves ``minutes`` into the past."""
    from app.domain.review.repositories import AcceptCardRepository

    def older(value):
        if isinstance(value, dict):
            return {k: older(v) for k, v in value.items()}
        if isinstance(value, str):
            try:
                moment = datetime.fromisoformat(value)
            except ValueError:
                return value
            return (moment - timedelta(minutes=minutes)).isoformat()
        return value

    async def rewind() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(uuid.UUID(card_id))
            card.merge_state = older(card.merge_state)
            await s.commit()

    asyncio.run(rewind())


def _a_refused_pr(client, app_world, monkeypatch, tmp_path, *, conflicting):
    fake = app_world["fake"]
    _, tid, cid, number, _ = _ready_card(client, app_world)
    pr = _forgejo_refusing(fake, monkeypatch, tmp_path, conflicting=conflicting)
    fake.prs[number]["head_sha"] = pr.head
    fake.check_state_by_sha[pr.head] = ("success", "全绿")
    _poll(client)
    wait_work_idle()
    return tid, cid, pr


def _poll_after(client, card_id: str, minutes: int) -> None:
    _time_passes(client, card_id, minutes)
    _poll(client)
    wait_work_idle()


def test_a_pr_that_merges_cleanly_is_never_a_conflict(
    client, app_world, monkeypatch, tmp_path
):
    tid, cid, pr = _a_refused_pr(
        client, app_world, monkeypatch, tmp_path, conflicting=False
    )
    _poll_after(client, cid, 10)
    assert _cards(client, tid)[0]["merge_state"]["state"] != "dirty"

    _git_has_answered(pr)
    _poll_after(client, cid, 10)

    assert _cards(client, tid)[0]["merge_state"]["state"] != "dirty"
    assert _nudges(client, tid, "pr_conflict") == []
    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "冲突" not in r.json()["message"]
    assert app_world["fake"].merge_calls == []


def test_a_pr_that_conflicts_is_a_conflict_once_git_has_merged_it(
    client, app_world, monkeypatch, tmp_path
):
    tid, cid, pr = _a_refused_pr(
        client, app_world, monkeypatch, tmp_path, conflicting=True
    )
    _git_has_answered(pr)
    _poll(client)
    wait_work_idle()

    assert _cards(client, tid)[0]["merge_state"]["state"] == "dirty"
    assert len(_nudges(client, tid, "pr_conflict")) == 1
