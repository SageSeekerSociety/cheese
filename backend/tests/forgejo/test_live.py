"""Opt-in contract test against the pinned, isolated Forgejo container."""

import asyncio
import base64
import json
import os
import shlex
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from app.api.routes.git_http import open_task_workspace, task_workspace
from app.api.routes.topics import _source_bytes
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.forgejo_tokens import ForgejoTokens, purge_expired_tokens
from app.domain.project.forge import (
    branch_head,
    ensure_repository_webhook,
    provision_repository,
    reconcile_repository_webhooks,
)
from app.domain.project.models import ForgeToken, Project
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review.forgejo_pr import ForgejoPRClient
from app.domain.review.merge_state import compute_merge_state
from app.domain.review.models import AcceptCard
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("FORGEJO_TEST_TOKEN_FILE"),
        reason="isolated Forgejo not configured",
    ),
]


@pytest.mark.parametrize("native", ["git", "fj"])
async def test_native_tools_through_platform_transport(
    db_factory, monkeypatch, tmp_path, native
):
    import socket

    from fastapi import FastAPI

    from app.api.routes.forge_token import router
    from app.core.db import get_db

    if native == "fj" and not os.environ.get("FORGEJO_TEST_FJ_BINARY"):
        pytest.skip("native fj binary not configured")

    url = "http://127.0.0.1:33086"
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(
        settings,
        "forgejo_admin_token",
        Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip(),
    )
    async with db_factory() as session:
        project = Project(name="Native Git transport test")
        session.add(project)
        await session.flush()
        binding = await provision_repository(project.id, session)
        await session.commit()
    token, _ = await ForgejoTokens(binding, sessions=db_factory).installation_token()
    app = FastAPI()
    app.include_router(router)

    async def database():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = database
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    remote = f"http://127.0.0.1:{port}/sandbox/forge/{project.id}/{binding.repo}.git"

    def clone_and_push():
        auth = base64.b64encode(f"project:{token}".encode()).decode()
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.extraHeader",
            GIT_CONFIG_VALUE_0="Authorization: Basic " + auth,
            GIT_TERMINAL_PROMPT="0",
        )

        def git(*args):
            return subprocess.run(
                ["git", *args],
                cwd=tmp_path,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()

        git("clone", remote, "checkout")
        checkout = tmp_path / "checkout"
        (checkout / "transport.bin").write_bytes(bytes(range(256)) * 4096)
        git("-C", str(checkout), "add", "transport.bin")
        git(
            "-C",
            str(checkout),
            "-c",
            "user.name=Agent",
            "-c",
            "user.email=agent@example.invalid",
            "commit",
            "-m",
            "Test transport",
        )
        git("-C", str(checkout), "push", "origin", "HEAD:refs/heads/task/transport")
        return git("-C", str(checkout), "rev-parse", "HEAD")

    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        head = await asyncio.to_thread(clone_and_push)
        if native == "git":
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{binding.api_url}/repos/{binding.repo}/branches/task/transport",
                    headers={"Authorization": "token " + token},
                )
                response.raise_for_status()
                assert response.json()["commit"]["id"] == head
        else:
            from app.domain.agent import forge_cli

            monkeypatch.setattr(
                forge_cli,
                "native_binary",
                lambda _: os.environ["FORGEJO_TEST_FJ_BINARY"],
            )
            monkeypatch.setattr(
                forge_cli,
                "credentials",
                lambda: {
                    "kind": "forgejo",
                    "project_id": str(project.id),
                    "url": f"http://127.0.0.1:1/{binding.repo}.git",
                    "repo": binding.repo,
                    "token": token,
                },
            )
            monkeypatch.setenv("CHEESE_API", f"http://127.0.0.1:{port}")
            assert await asyncio.to_thread(forge_cli.run, "fj", ["whoami"]) == 0
            assert (
                await asyncio.to_thread(
                    forge_cli.run,
                    "fj",
                    [
                        "pr",
                        "create",
                        "Transport PR",
                        "--repo",
                        binding.repo,
                        "--base",
                        "main",
                        "--head",
                        "task/transport",
                        "--body",
                        "Native CLI through relay",
                    ],
                )
                == 0
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{binding.api_url}/repos/{binding.repo}/pulls",
                    headers={"Authorization": "token " + token},
                )
                response.raise_for_status()
                [pr] = response.json()
                assert pr["title"] == "Transport PR"
                assert pr["head"]["sha"] == head
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        listener.close()


async def test_legacy_repository_migration_preserves_all_refs(
    db_factory, monkeypatch, tmp_path
):
    from app.core.storage import LocalStorageBackend
    from app.domain.project.forge_migration import git, references
    from app.domain.room_task import snapshots
    from scripts import migrate_forge
    from tests.unit.test_forge_migration import legacy_repository

    url = "http://127.0.0.1:33086"
    admin = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(settings, "forgejo_admin_token", admin)
    root = tmp_path / "workspaces"
    monkeypatch.setattr(settings, "workspace_root", str(root))
    monkeypatch.setattr(migrate_forge, "async_session_factory", db_factory)
    storage = LocalStorageBackend(str(tmp_path / "private"), "unused-private-url")
    monkeypatch.setattr(snapshots, "transcript_storage", lambda: storage)
    async with db_factory() as session:
        project = Project(name="Legacy repository migration test")
        session.add(project)
        await session.flush()
        room = Topic(project_id=project.id, title="Legacy room")
        session.add(room)
        await session.flush()
        task = Task(
            project_id=project.id,
            room_id=room.id,
            title="Unfinished task",
            branch_name="task",
            workspace_name="task",
            base_branch="main",
        )
        session.add(task)
        await session.commit()
    source, task_worktree = legacy_repository(root, project.id)
    before = references(source)
    backups = tmp_path / "backups"
    monkeypatch.setattr(
        "sys.argv",
        [
            "migrate_forge",
            "--backup-root",
            str(backups),
            "--project",
            str(project.id),
            "--check",
        ],
    )
    with pytest.raises(SystemExit) as pending:
        await migrate_forge.main()
    assert pending.value.code == 2
    real_push = migrate_forge.push

    def interrupted_after_push(*args):
        real_push(*args)
        raise RuntimeError("simulated interruption after remote push")

    monkeypatch.setattr(migrate_forge, "push", interrupted_after_push)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        await migrate_forge.migrate(project.id, backups, apply=True)
    async with db_factory() as session:
        assert await migrate_forge.binding_for_project(project.id, session) is None
    monkeypatch.setattr(migrate_forge, "push", real_push)
    assert await migrate_forge.migrate(project.id, backups, apply=True) == "migrated"
    assert await migrate_forge.migrate(project.id, backups, apply=True) == "skipped"
    await migrate_forge.main()  # A later release can proceed without stopping writers.
    assert references(source) == before
    assert (task_worktree / "new.txt").read_bytes() == b"untracked\x00bytes"
    async with db_factory() as session:
        assert (
            await branch_head(project.id, session, "main") == before["refs/heads/main"]
        )
        assert (
            await branch_head(project.id, session, "task") == before["refs/heads/task"]
        )
        snapshot = await snapshots.latest(session, task.id)
        data = await snapshots.download(snapshot)
    bundle = tmp_path / "downloaded.bundle"
    bundle.write_bytes(data)
    recovered = tmp_path / "recovered"
    git(tmp_path, "clone", str(source), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task.id}")
    git(recovered, "checkout", "--detach", snapshot.snapshot_sha)
    assert (recovered / "new.txt").read_bytes() == b"untracked\x00bytes"


async def test_new_repository_registers_forgejo_events(db_factory, monkeypatch):
    url = "http://127.0.0.1:33086"
    admin = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(settings, "forgejo_admin_token", admin)
    hook_url = "https://relay.example.invalid/forge/events/test"
    monkeypatch.setattr(settings, "forge_webhook_url", hook_url)
    monkeypatch.setattr(settings, "forge_event_secret", "isolated-hook-secret")
    async with db_factory() as session:
        project = Project(name="Forgejo event registration test")
        session.add(project)
        await session.flush()
        binding = await provision_repository(project.id, session)
        await session.commit()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{binding.api_url}/repos/{binding.repo}/hooks",
            headers={"Authorization": "token " + admin},
        )
    response.raise_for_status()
    (hook,) = response.json()
    assert hook["active"]
    assert hook["type"] == "forgejo"
    assert hook["config"]["url"] == f"{hook_url}/{project.id}"
    assert {"push", "pull_request", "action_run_success", "action_run_failure"} <= set(
        hook["events"]
    )
    async with httpx.AsyncClient() as client:
        endpoint = f"{binding.api_url}/repos/{binding.repo}/hooks"
        headers = {"Authorization": "token " + admin}
        response = await client.delete(f"{endpoint}/{hook['id']}", headers=headers)
        response.raise_for_status()
        old = await client.post(
            endpoint,
            headers=headers,
            json={
                "type": "forgejo",
                "active": False,
                "events": ["push"],
                "config": {"url": f"{hook_url}/{project.id}", "content_type": "json"},
            },
        )
        old.raise_for_status()
        assert await reconcile_repository_webhooks(db_factory) == {
            "configured": 1,
            "failed": 0,
        }
        await ensure_repository_webhook(binding)
        response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        (repaired,) = response.json()
        assert repaired["active"]
        assert repaired["id"] != old.json()["id"]
        assert set(hook["events"]) <= set(repaired["events"])


async def test_forgejo_delivery_reaches_outbound_deployment(db_factory, monkeypatch):
    """Requires host.docker.internal:33087 to reach this test's loopback relay."""
    from app import forge_events_app as relay
    from app.domain.review import events

    url = "http://127.0.0.1:33086"
    admin = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    secret = "isolated-live-event-secret"
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(settings, "forgejo_admin_token", admin)
    monkeypatch.setattr(
        settings,
        "forge_webhook_url",
        "http://host.docker.internal:33087/forge/events/test",
    )
    monkeypatch.setattr(settings, "forge_event_secret", secret)
    monkeypatch.setattr(settings, "forge_event_relay_keys", {"test": secret})
    monkeypatch.setattr(
        settings,
        "forge_event_relay_url",
        "ws://127.0.0.1:33087/forge/events/test/connect",
    )
    server = uvicorn.Server(
        uvicorn.Config(relay.app, host="127.0.0.1", port=33087, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    consumer = None
    scheduler = AsyncMock()
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        consumer = asyncio.create_task(events.listen(scheduler, db_factory))
        async with asyncio.timeout(10):
            while "test" not in relay.connections:
                await asyncio.sleep(0.01)
        async with db_factory() as session:
            project = Project(name="Forgejo outbound event delivery test")
            session.add(project)
            await session.flush()
            binding = await provision_repository(project.id, session)
            await session.commit()
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": "token " + admin}
            hooks = await client.get(
                f"{binding.api_url}/repos/{binding.repo}/hooks", headers=headers
            )
            hooks.raise_for_status()
            hook_id = hooks.json()[0]["id"]
            delivered = await client.post(
                f"{binding.api_url}/repos/{binding.repo}/hooks/{hook_id}/tests",
                headers=headers,
                json={"ref": "main"},
            )
            delivered.raise_for_status()
        async with asyncio.timeout(20):
            while not scheduler.forge_repository_changed.await_count:
                await asyncio.sleep(0.05)
        scheduler.forge_repository_changed.assert_awaited_with(
            kind="forgejo", repo=binding.repo, project_id=str(project.id)
        )
    finally:
        if consumer is not None:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(serving, 10)


def _push_with_cli(binding, token, root, author):
    class TokenEndpoint(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/sandbox/forge-token"
            body = json.dumps(
                {
                    "data": {
                        "url": binding.url,
                        "username": binding.repo.split("/")[0],
                        "token": token,
                    }
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), TokenEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cli = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
    helper = f"!{shlex.quote(sys.executable)} {shlex.quote(str(cli))} git-credential"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        CHEESE_API=f"http://127.0.0.1:{server.server_port}", GIT_TERMINAL_PROMPT="0"
    )

    def git(*args):
        result = subprocess.run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                f"credential.helper={helper}",
                "-c",
                "credential.useHttpPath=true",
                *args,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    try:
        git("clone", binding.url, "checkout")
        git("-C", "checkout", "switch", "-c", "task/report")
        (root / "checkout" / "report.md").write_text("first report\n")
        (root / "checkout" / "assets").mkdir()
        (root / "checkout" / "assets" / "sample.bin").write_bytes(b"\x00\xff\x01")
        git("-C", "checkout", "add", "report.md", "assets/sample.bin")
        git(
            "-C",
            "checkout",
            "-c",
            f"user.name={author.name}",
            "-c",
            f"user.email={author.email}",
            "commit",
            "-m",
            "Write report",
        )
        git("-C", "checkout", "push", "origin", "task/report")
        return git("-C", "checkout", "rev-parse", "HEAD")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("requester_credit", [True, False])
async def test_project_proposal_lifecycle_and_credential_cache_cleanup(
    db_factory, monkeypatch, tmp_path, requester_credit
):
    from app.api.routes.accept import _pr_checks_payload
    from app.domain.review import services as review_services
    from app.domain.site.services import (
        publication_source,
        publish_site,
        read_release_file,
    )

    monkeypatch.setattr(review_services, "async_session_factory", db_factory)
    url = "http://127.0.0.1:33086"
    admin = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(settings, "forgejo_admin_token", admin)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "platform-storage"))
    async with db_factory() as session:
        project = Project(name="Forgejo integration test", owner_handle="requester")
        session.add(project)
        await session.flush()
        binding = await provision_repository(project.id, session)
        room = Topic(project_id=project.id, title="Report room", created_by="requester")
        session.add(room)
        await session.flush()
        task = Task(
            project_id=project.id,
            room_id=room.id,
            title="Report task",
            branch_name="task/report",
            base_branch="main",
            created_by="cheese",
        )
        session.add(task)
        await session.commit()
        scoped = mint_scoped_token(project_id=str(project.id), topic_id=str(room.id))
        metadata = await open_task_workspace(project.id, task.id, session, scoped)
        assert metadata["data"]["coauthors"] == ["requester <requester@zhishi.local>"]
        project.settings = {"forge_requester_coauthor": requester_credit}
        await session.commit()
        metadata = await task_workspace(project.id, task.id, session, scoped)
        assert metadata["data"]["coauthors"] == (
            ["requester <requester@zhishi.local>"] if requester_credit else []
        )
        from app.domain.repository.identity import attribution

        author = (await attribution(session, room, task_id=task.id)).author
        assert author is not None
    tokens = ForgejoTokens(binding, sessions=db_factory)
    token, _ = await tokens.installation_token()
    owner, repo = binding.repo.split("/", 1)
    publisher = ForgejoPRClient(owner, repo, tokens, api_base=binding.api_url)
    reviewed = await asyncio.to_thread(_push_with_cli, binding, token, tmp_path, author)
    chat = object.__new__(ChatService)
    chat._sessions = db_factory
    baseline = await chat._known_commits(binding.project_id, room.id)
    assert baseline == {reviewed}
    async with db_factory() as session:
        assert await branch_head(binding.project_id, session, "task/report") == reviewed
        files = ProjectFiles(session, binding.project_id, task.id)
        assert set(await files.changed_files()) == {"report.md", "assets/sample.bin"}
        history = await files.history()
        assert len(history) == 1 and history[0]["message"] == "Write report"
    async with httpx.AsyncClient(
        base_url=binding.api_url,
        headers={"Authorization": f"token {token}"},
    ) as client:
        forbidden = await client.get("/admin/users")
        assert forbidden.status_code == 403
        draft = await publisher.open_pr(
            head="task/report",
            base="main",
            title="WIP: Report",
            body="Report",
            draft=True,
        )
        draft = draft.pr
        assert draft["draft"]
        async with db_factory() as session:
            recorded = await session.get(Task, task.id)
            recorded.pr_number = draft["number"]
            await session.commit()
            diff = await ProjectFiles(session, binding.project_id, task.id).diff(
                "committed"
            )
            assert "diff --git a/report.md b/report.md" in diff
            assert "+first report" in diff
        await publisher.mark_ready_for_review(draft["node_id"])
        assert not (await publisher.pr_view(draft["number"]))["draft"]
        updated = await client.post(
            f"/repos/{binding.repo}/contents/appendix.md",
            json={
                "branch": "task/report",
                "content": base64.b64encode(b"appendix\n").decode(),
                "message": "Add appendix",
                "author": {"name": author.name, "email": author.email},
            },
        )
        assert updated.status_code == 201, updated.text
        latest = updated.json()["commit"]["sha"]
        check_url = "https://checks.example.test/report/1"
        check = await client.post(
            f"/repos/{binding.repo}/statuses/{latest}",
            json={
                "state": "success",
                "context": "report-test",
                "target_url": check_url,
            },
        )
        assert check.status_code == 201, check.text
        changes = await chat._turn_changeset(binding.project_id, room.id, baseline)
        assert changes is not None and changes.commits == [latest]
        assert changes.files == [{"path": "appendix.md", "added": 1, "removed": 0}]
        async with db_factory() as session:
            card = AcceptCard(
                topic_id=room.id,
                task_id=task.id,
                reviewer_handle="reviewer",
                pr_number=draft["number"],
                pr_head_sha=reviewed,
                pr_repo=binding.repo,
                pr_url=draft["url"],
                rebase_count=2,
            )
            session.add(card)
            await session.commit()
            checks = await _pr_checks_payload(room.id, session, task_id=task.id)
            assert checks["available"] is True
            assert checks["checks"] == [
                {
                    "name": "report-test",
                    "status": "completed",
                    "conclusion": "success",
                    "url": check_url,
                }
            ]
            refreshed = await AcceptService(session).push_fix(task.id)
            assert refreshed["pushed"] is True
            assert card.pr_head_sha == latest and card.rebase_count == 0
            assert (await AcceptService(session).push_fix(task.id))["pushed"] is False
            await session.commit()
        args = dict(owner=owner, repo=repo, number=draft["number"], token=token)
        stale = await publisher.client.merge_pull_request(**args, sha=reviewed)
        assert stale.stale_head and stale.sha is None
        for _ in range(30):
            status = await publisher.client.pull_request_status(**args)
            if status.mergeable:
                break
            await asyncio.sleep(0.2)
        verdict = compute_merge_state(
            github_mergeable_state=status.mergeable_state,
            github_mergeable=status.mergeable,
            check_runs=[],
            required_checks=[],
        )
        assert verdict.state == "clean"
        async with db_factory() as session:
            accepted = await AcceptService(session).accept(
                card_id=card.id, decided_by="reviewer", head_sha=latest
            )
            await session.commit()
            assert accepted.status.value == "accepted"
            merged_sha = accepted.pr_head_sha
            delivered = await session.get(Task, task.id)
            assert delivered.status == TaskStatus.closed
            assert delivered.accepted_by == "reviewer"
            assert delivered.delivered_head == latest
            assert (await session.get(Topic, room.id)).status.value == "active"
        assert (await publisher.client.pull_request_status(**args)).merged
        merged_commit = await client.get(
            f"/repos/{binding.repo}/git/commits/{merged_sha}"
        )
        assert merged_commit.status_code == 200, merged_commit.text
        message = merged_commit.json()["commit"]["message"]
        merged_author = merged_commit.json()["commit"]["author"]
        assert merged_author["name"] == author.name
        assert merged_author["email"] == author.email
        assert (
            "Co-authored-by: requester <requester@zhishi.local>" in message
        ) == requester_credit
        assert f"Cheese-Card: {card.id}" in message
        assert f"/topics/{room.id}" in message
        assert f"card={task.id}" in message
        assert "Reviewed-by: reviewer <reviewer@zhishi.local>" in message
        async with db_factory() as session:
            files = ProjectFiles(session, binding.project_id, None)
            listed, source = await files.files("committed")
            assert source == "committed"
            assert {item["path"] for item in listed} >= {
                "report.md",
                "appendix.md",
                "assets/sample.bin",
            }
            report = await files.text("report.md", "committed")
            assert report["content"] == "first report\n"
            assert report["editable"] is False
            binary = await files.text("assets/sample.bin", "committed")
            assert binary["binary"] and binary["content"] is None
            raw, source = await files.raw("assets/sample.bin", "committed")
            assert raw == b"\x00\xff\x01" and source == "committed"
            # Project documents use the same committed bytes as the file tree,
            # even when no task is selected and the room has no such attachment.
            assert (
                await _source_bytes(
                    session,
                    binding.project_id,
                    room.id,
                    "assets/sample.bin",
                    None,
                    "committed",
                )
                == raw
            )
            assert "+appendix" in await files.diff("committed")
            assert any(row["hash"] == merged_sha[:12] for row in await files.history())
        deleted = await client.delete(f"/repos/{binding.repo}/branches/task/report")
        assert deleted.status_code == 204
        async with db_factory() as session:
            delivered_files = ProjectFiles(session, binding.project_id, task.id)
            retained = await delivered_files.text("report.md", "live")
            assert retained["content"] == "first report\n"
            assert retained["source"] == "committed" and not retained["editable"]
        website = await client.post(
            f"/repos/{binding.repo}/contents/index.html",
            json={
                "branch": "main",
                "content": base64.b64encode(b"<h1>Published</h1>").decode(),
                "message": "Add static website",
            },
        )
        assert website.status_code == 201, website.text
        site_revision = website.json()["commit"]["sha"]
        async with db_factory() as session:
            source = await publication_source(session, project.id)
            assert source == {
                "source_revision": site_revision,
                "candidates": [{"directory": ".", "entry_file": "index.html"}],
            }
            release = await publish_site(
                session,
                project.id,
                handle="requester",
                directory=".",
                expected_source_revision=site_revision,
            )
            await session.commit()
            assert read_release_file(release, "index.html") == b"<h1>Published</h1>"
            assert read_release_file(release, "assets/sample.bin") == b"\x00\xff\x01"
        changed_site = await client.put(
            f"/repos/{binding.repo}/contents/index.html",
            json={
                "branch": "main",
                "sha": website.json()["content"]["sha"],
                "content": base64.b64encode(b"<h1>Next version</h1>").decode(),
                "message": "Update static website",
            },
        )
        assert changed_site.status_code == 200, changed_site.text
        assert read_release_file(release, "index.html") == b"<h1>Published</h1>"
        async with db_factory() as session:
            lease = await session.scalar(
                select(ForgeToken).where(ForgeToken.project_id == binding.project_id)
            )
            lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        assert (await purge_expired_tokens(db_factory))["deleted"] == 1
        # Cache cleanup cannot revoke a token before its provider expiration.
        retained = await client.get(f"/repos/{binding.repo}")
        assert retained.status_code == 200
