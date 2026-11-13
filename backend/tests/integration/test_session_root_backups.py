"""Root backups follow the session; oversized diffs invalidate older backups."""

import asyncio
import hashlib
import io
import urllib.request
import uuid
from types import SimpleNamespace

import pytest

from app.core.storage import S3StorageBackend
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import root_snapshots
from app.domain.machine.models import SandboxRootSnapshot
from tests.executor_release import running
from tests.integration.test_switch_pushes_first import _machines, _room
from tests.support.files_bucket import files_bucket

pytestmark = pytest.mark.anyio


async def test_root_notice_waits_for_a_tool_and_is_delivered_once(client, monkeypatch):
    room = await _room(client)
    _machines(monkeypatch)
    notice = root_snapshots.NOTICES["image_changed"]
    async with client.test_factory() as db:
        session = await AgentSessionService(db).by_id(room.session_id)
        session.work_lease = {**session.work_lease, "root_snapshot_notice": notice}
        await db.commit()

    async def execute(target, method, params, **kwargs):
        return {
            **running(),
            "state": "/old/state",
            "workspace": "/old/work",
            "mcp_servers": [],
        }

    monkeypatch.setattr(execution, "call", execute)

    def acquire(tells_agent):
        response = client.post(
            room.lease_path,
            headers=room.agent,
            json={"tells_agent": tells_agent},
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    assert notice not in acquire(False).get("notice", "")
    assert notice in acquire(True)["notice"]
    assert notice not in acquire(True).get("notice", "")


async def test_a_saved_root_can_be_downloaded_after_the_home_is_gone(
    client, monkeypatch
):
    room = await _room(client, on_cloud=True)
    home = SimpleNamespace(
        session_id=room.session_id,
        host_id=uuid.uuid4(),
        sandbox_image="ghcr.io/example/root@sha256:" + "1" * 64,
        project_id=room.project_id,
        topic_id=room.topic_id,
        resource_id=str(uuid.uuid4()),
    )
    with files_bucket() as config:
        bucket = S3StorageBackend(
            bucket=config["files_bucket"],
            endpoint_url=config["files_bucket_endpoint_url"],
            access_key="test",
            secret_key="test",
        )
        monkeypatch.setattr(root_snapshots, "private_storage", lambda: bucket)
        body = b"root diff bytes"

        async def host(hub, device, action, *, url, **kwargs):
            def upload():
                request = urllib.request.Request(
                    url,
                    data=body,
                    method="PUT",
                    headers={
                        "Content-Type": "application/gzip",
                        "Content-Length": str(len(body)),
                    },
                )
                with urllib.request.urlopen(request) as response:
                    assert response.status == 200

            await asyncio.to_thread(upload)
            return {
                "status": "saved",
                "size": len(body),
                "md5": hashlib.md5(body).hexdigest(),
            }

        async with client.test_factory() as db:
            await root_snapshots.save(db, home, "source", hub=None, run_on_host=host)
        del home
        async with client.test_factory() as db:
            restored = await root_snapshots.for_restore(db, room.session_id)

        def download():
            with urllib.request.urlopen(restored["url"]) as response:
                return response.read()

        assert await asyncio.to_thread(download) == body
        assert restored["image"].endswith("1" * 64)


async def test_an_oversized_root_does_not_offer_an_older_backup(client, monkeypatch):
    room = await _room(client, on_cloud=True)
    home = SimpleNamespace(
        session_id=room.session_id,
        host_id=uuid.uuid4(),
        sandbox_image="ghcr.io/example/root@sha256:" + "1" * 64,
        project_id=room.project_id,
        topic_id=room.topic_id,
        resource_id=str(uuid.uuid4()),
    )
    with files_bucket() as config:
        bucket = S3StorageBackend(
            bucket=config["files_bucket"],
            endpoint_url=config["files_bucket_endpoint_url"],
            access_key="test",
            secret_key="test",
        )
        monkeypatch.setattr(root_snapshots, "private_storage", lambda: bucket)
        async with client.test_factory() as db:
            await bucket.upload(io.BytesIO(b"old"), "old-root", "application/gzip")
            db.add(
                SandboxRootSnapshot(
                    session_id=room.session_id,
                    image=home.sandbox_image,
                    key="old-root",
                    size=3,
                    md5=hashlib.md5(b"old").hexdigest(),
                    status="saved",
                )
            )
            await db.commit()

            async def oversized(*args, **kwargs):
                return {"status": "too_large"}

            await root_snapshots.save(
                db, home, "source", hub=None, run_on_host=oversized
            )
        async with client.test_factory() as db:
            assert await root_snapshots.for_restore(db, room.session_id) == {
                "status": "too_large"
            }
        assert not await bucket.exists("old-root")
