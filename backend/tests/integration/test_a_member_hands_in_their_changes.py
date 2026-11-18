"""A member hands a task's changes in on the road a teammate's hand-in takes.

A person who saved an edit in a task's 「改动」 tab gets it into the project
only as a version of the task's delivery: the same card, reviewers and accept.
The task's sandbox is a checkout on disk here, cloned from the test forge's
git store, and its executor runs commands in a real shell there; the forge's
pull request follows the branch the way the real one does.
"""

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent import execution
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.claude_code.remote_execution import runtime_context
from app.domain.agent_session.services import AgentSessionService
from app.domain.review import pr_publish
from app.domain.review.repositories import AcceptCardRepository
from tests.delivery import delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import (
    _accept,
    _arm,
    _cards,
    _give_card_a_pr,
    _make_card,
    _make_project,
    _make_topic,
    _poll,
    _protect,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.support import git_store

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

ALICE = session_auth_headers("alice")


class Sandbox:
    """The task's sandbox: its checkout under a home of its own, and the
    executor that runs the platform's commands and file operations there."""

    def __init__(self, root: Path, project_id: uuid.UUID, task_id: uuid.UUID, fake):
        self.task_id, self.fake = task_id, fake
        self.repo = git_store.ensure_repo(project_id)
        self.branch = git_store.branch_for_task(task_id)
        self.home = root / "home"
        self.checkout = self.home / ".cheese" / "tasks" / str(task_id)
        self.checkout.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "-q", str(self.repo), str(self.checkout)], check=True
        )
        self.git("checkout", "-q", "-B", self.branch, f"origin/{self.branch}")
        self.git("config", "user.name", "芝士")
        self.git("config", "user.email", "cheese@zhishi.local")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        # The sandbox CLI's push, `cheese sync --task <id>`.
        (bin_dir / "cheese").write_text(
            '#!/bin/sh\n[ "$1" = sync ] || exit 0\n'
            'cd "$HOME/.cheese/tasks/$3" && '
            'git push -q origin "HEAD:refs/heads/$(git rev-parse --abbrev-ref HEAD)"\n'
        )
        (bin_dir / "cheese").chmod(0o755)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.checkout), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    async def call(self, target, method, params, *, timeout=660, **_):
        if method == "task_fs":
            host = SimpleNamespace(
                env={"HOME": str(self.home)},
                root=self.home,
                invoke=lambda _p: {},
                log=lambda *a, **k: None,
            )
            return runtime_context.task_fs(host, params)
        assert method == "invoke", method
        done = subprocess.run(
            ["bash", "-c", params["args"]["command"]],
            env={**os.environ, "HOME": str(self.home), "PATH": self.path},
            capture_output=True,
            text=True,
            timeout=120,
        )
        said = done.stdout + done.stderr
        if "cheese sync" in params["args"]["command"]:
            # The forge's pull request on this branch follows what was pushed.
            head = git_store.git(self.repo, "rev-parse", self.branch).strip()
            for pr in self.fake.prs.values():
                if pr["head"] == self.branch:
                    pr["head_sha"] = head
        if done.returncode:
            said = f"Exit code {done.returncode}\n{said}"
        return {"value": {"stdout": said}}


@pytest.fixture
def world(client, app_world, monkeypatch, tmp_path):
    """A project, a task whose teammate committed once, and its sandbox."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    task_id = delivery_task_id(client, tid)
    sandbox = Sandbox(tmp_path, uuid.UUID(pid), task_id, app_world["fake"])
    monkeypatch.setattr(execution, "call", sandbox.call)

    async def held(self, conversation_id, generation):
        return {"kind": "device", "device_id": "box-1"}, False

    monkeypatch.setattr(AgentSessionService, "working_copy", held)
    return SimpleNamespace(pid=pid, tid=tid, task_id=task_id, sandbox=sandbox)


def _save(client, world, path: str, content: str) -> None:
    query = f"task={world.task_id}&path={path}"
    read = client.get(f"/projects/{world.pid}/file?{query}", headers=ALICE)
    assert read.status_code == 200, read.text
    data = read.json()["data"]
    saved = client.put(
        f"/projects/{world.pid}/file?task={world.task_id}",
        json={
            "path": path,
            "content": content,
            "version": data["version"],
            "base": data["content"],
        },
        headers=ALICE,
    )
    assert saved.status_code == 200, saved.text


def _hand_in(client, world, **body):
    return client.post(
        f"/topics/{world.task_id}/hand-in",
        json={"subject": "Say hello in the delivery note", **body},
        headers=ALICE,
    )


def _card(client, card_id: str):
    async def read():
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(uuid.UUID(card_id))
            reviewers = await AcceptCardRepository(s).reviewer_handles(card.id)
            return card, reviewers

    return asyncio.run(read())


def _note(world) -> str:
    return f"deliveries/{world.task_id}.txt"


def test_a_saved_edit_handed_in_is_a_version_by_the_member(client, world):
    _save(client, world, _note(world), "Hello from alice\n")

    r = _hand_in(client, world, body="A friendlier first line.")

    assert r.status_code == 200, r.text
    card_id = r.json()["data"]["card"]["id"]
    card, reviewers = _card(client, card_id)
    assert card.status == "pending"
    assert card.change_subject == "Say hello in the delivery note"
    # Nobody named a reviewer: the task's own (the ladder's second rung).
    assert reviewers == ["alice"]
    repo, branch = world.sandbox.repo, world.sandbox.branch
    tip = git_store.git(repo, "log", "-1", "--format=%an", branch).strip()
    assert tip == "alice"
    shown = git_store.git(repo, "show", f"{branch}:{_note(world)}")
    assert shown == "Hello from alice\n"


def test_nothing_new_is_refused(client, world):
    _save(client, world, _note(world), "Hello from alice\n")
    assert _hand_in(client, world).status_code == 200

    again = _hand_in(client, world)

    assert again.status_code == 422, again.text
    assert again.json()["error"]["i18n"]["key"] == "handInNothingNew"


def test_a_teammate_at_work_is_not_committed_under(client, world, monkeypatch):
    _save(client, world, _note(world), "Hello from alice\n")
    before = git_store.git(world.sandbox.repo, "rev-parse", world.sandbox.branch)

    async def working(self, topic_id, agent_handle=None):
        return True

    monkeypatch.setattr(ChatService, "has_running_turn", working)

    r = _hand_in(client, world)

    assert r.status_code == 409, r.text
    after = git_store.git(world.sandbox.repo, "rev-parse", world.sandbox.branch)
    assert after == before
    assert world.sandbox.git("status", "--porcelain")


def test_a_reviewer_who_adjusted_a_waiting_version_adopts_the_adjusted_one(
    client, world, app_world
):
    fake = app_world["fake"]
    card_id = _make_card(client, world.tid)
    old_head = _give_card_a_pr(client, app_world, world.tid, card_id, 7)
    _save(client, world, _note(world), "Tweaked by the reviewer\n")

    refused = _accept(client, card_id, head_sha=old_head)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "acceptUncommitted"

    again = _hand_in(client, world, subject="chore(test): file an accept card")
    assert again.status_code == 200, again.text
    card, _ = _card(client, card_id)
    assert card.pr_head_sha != old_head
    assert _accept(client, card_id, head_sha=old_head).status_code == 422

    fake.check_state_by_sha[card.pr_head_sha] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    adopted = _accept(client, card_id, head_sha=card.pr_head_sha)
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["data"]["status"] == "accepted"
    shown = git_store.git(
        world.sandbox.repo, "show", f"{card.pr_head_sha}:{_note(world)}"
    )
    assert shown == "Tweaked by the reviewer\n"


def test_adopting_ones_own_hand_in_completes_once_the_forge_says_it_merges(
    client, world, app_world, monkeypatch
):
    """A head pushed a moment ago has no merge verdict yet: 「提交并采纳」 is not
    refused for that, and the version is merged under the person's name as
    soon as the forge says it merges."""
    fake = app_world["fake"]
    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    seed = fake.seed_pr

    def opened(number, *, head, **kwargs):
        seed(number, head=head, **kwargs)
        real = git_store.git(world.sandbox.repo, "rev-parse", head).strip()
        fake.prs[number]["head_sha"] = real
        fake.check_state_by_sha[real] = ("success", "全部通过")
        fake.merge_sha_by_number[number] = "merge-sha-1"
        # The forge has not computed whether the new head merges.
        fake.mergeable_state_by_number[number] = "unknown"
        return real

    monkeypatch.setattr(fake, "seed_pr", opened)
    _save(client, world, _note(world), "Hello from alice\n")

    r = _hand_in(client, world, reviewer_handle="alice", accept=True)

    assert r.status_code == 200, r.text
    card_id = r.json()["data"]["card"]["id"]
    card, _ = _card(client, card_id)
    assert card.status == "pending"
    assert fake.merge_calls == []

    del fake.mergeable_state_by_number[card.pr_number]
    _poll(client)

    card, _ = _card(client, card_id)
    assert card.status == "accepted"
    assert card.decided_by == "alice"
    # What was merged is the branch as handed in, with the saved edit.
    [merged] = [m["sha"] for m in fake.merge_calls]
    shown = git_store.git(world.sandbox.repo, "show", f"{merged}:{_note(world)}")
    assert shown == "Hello from alice\n"


def _settled_lines(client, task_id) -> list[str]:
    """The lines people read in the task's conversation, once the notices
    sent off in the background have landed."""
    from app.core import background

    for _ in range(200):
        if not background.inflight_count():
            break
        time.sleep(0.02)
    blocks = client.get(f"/topics/{task_id}/blocks").json()["data"]["data"]
    return [
        b["content"]
        for b in blocks
        if b["kind"] == "event" and (b.get("meta") or {}).get("in_room") is not False
    ]


def test_handing_in_another_version_says_so_in_one_line(client, world, app_world):
    """One action, one line: the task's conversation records that the member
    handed in another version, with its title, and nothing else about it."""
    card_id = _make_card(client, world.tid)
    _give_card_a_pr(client, app_world, world.tid, card_id, 7)
    _save(client, world, _note(world), "Tweaked by the reviewer\n")
    before = _settled_lines(client, world.task_id)

    r = _hand_in(client, world, subject="docs: greet the reader", body="Warmer.")

    assert r.status_code == 200, r.text
    added = _settled_lines(client, world.task_id)[len(before) :]
    assert len(added) == 1, added
    assert "docs: greet the reader" in added[0]
    assert "alice" in added[0]


def test_an_armed_version_is_not_merged_past_a_change_saved_after_it(
    client, world, app_world
):
    """A version armed to merge when its checks pass is adopted as it was:
    a change a person saved since is not in it, so the poller does not merge
    it, as an accept would not."""
    fake = app_world["fake"]
    card_id = _make_card(client, world.tid)
    head = _give_card_a_pr(client, app_world, world.tid, card_id, 7)
    _protect(client, world.pid, auto_merge_allowed=True)
    fake.check_state_by_sha[head] = ("pending", "还在跑")
    _poll(client)
    assert _arm(client, card_id, "alice").status_code == 200

    _save(client, world, _note(world), "Saved after the arm\n")
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    _poll(client)

    assert fake.merge_calls == []
    assert _cards(client, world.tid)[0]["status"] == "pending"


def test_an_ai_teammate_does_not_hand_in_on_a_members_road(client, world):
    from tests.delivery import delivery_headers

    r = client.post(
        f"/topics/{world.task_id}/hand-in",
        json={"subject": "Anything"},
        headers=delivery_headers(client, world.tid),
    )

    assert r.status_code == 403, r.text
