"""Root backups follow the session; oversized diffs invalidate older backups."""

import asyncio
import hashlib
import io
import json
import urllib.request
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.storage import S3StorageBackend
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import lease_claim, root_snapshots, session_work
from app.domain.machine.models import CloudHost, SandboxHome, SandboxRootSnapshot
from tests.executor_release import running
from tests.integration.test_switch_pushes_first import _machines, _room
from tests.support.files_bucket import files_bucket

pytestmark = pytest.mark.anyio


async def _source_home(db, room):
    host = CloudHost(
        customer_id=1,
        account_id=1,
        offering_id=1,
        hostname="test-host",
        login_user="test",
        cores=4,
        memory_mb=8192,
        disk_gb=40,
    )
    db.add(host)
    await db.flush()
    home = SandboxHome(
        session_id=room.session_id,
        host_id=host.id,
        sandbox_image="ghcr.io/example/root@sha256:" + "1" * 64,
        project_id=room.project_id,
        topic_id=room.topic_id,
        room_resource_id=room.lease["room_resource_id"],
        resource_id=str(uuid.uuid4()),
        spec="standard",
        stopped_at=datetime.now(UTC),
    )
    db.add(home)
    await db.commit()
    return home


async def test_a_late_save_cannot_replace_the_root_of_a_new_home(client, monkeypatch):
    room = await _room(client, on_cloud=True)
    async with client.test_factory() as db:
        old = await _source_home(db, room)
    reached, release = asyncio.Event(), asyncio.Event()
    with files_bucket() as config:
        bucket = S3StorageBackend(
            bucket=config["files_bucket"],
            endpoint_url=config["files_bucket_endpoint_url"],
            access_key="test",
            secret_key="test",
        )
        monkeypatch.setattr(root_snapshots, "private_storage", lambda: bucket)
        stat = bucket.stat

        async def delayed_stat(key):
            if not reached.is_set():
                reached.set()
                await release.wait()
            return await stat(key)

        monkeypatch.setattr(bucket, "stat", delayed_stat)

        async def host(hub, device, action, *, url, resource, **kwargs):
            body = b"old-root" if resource == old.resource_id else b"new-root"

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

        async def save_old():
            async with client.test_factory() as db:
                return await root_snapshots.save(
                    db, old, "source", hub=None, run_on_host=host
                )

        pending = asyncio.create_task(save_old())
        try:
            await asyncio.wait_for(reached.wait(), timeout=5)
            async with client.test_factory() as db:
                leaving = await db.get(SandboxHome, old.id)
                leaving.session_id = None
                await db.commit()
                current = await _source_home(db, room)
                await root_snapshots.save(
                    db, current, "target", hub=None, run_on_host=host
                )
                latest = await db.get(SandboxRootSnapshot, room.session_id)
                latest_key = latest.key
            release.set()
            await pending
            async with client.test_factory() as db:
                kept = await db.get(SandboxRootSnapshot, room.session_id)
                assert kept.key == latest_key
            assert await bucket.exists(latest_key)
        finally:
            release.set()
            await pending


@pytest.mark.parametrize("restoring", [False, True])
async def test_root_restore_can_outlast_an_ordinary_install(monkeypatch, restoring):
    monkeypatch.setattr(lease_claim, "INSTALL_MOST_S", 0.01)
    monkeypatch.setattr(root_snapshots, "RESTORE_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(session_work.launch, "script", lambda *args, **kwargs: "")

    async def host(*args, timeout, **kwargs):
        async def finish():
            await asyncio.sleep(0.04)
            return {"exit": 0, "stdout": json.dumps({"ready": True})}

        return await asyncio.wait_for(finish(), timeout=timeout)

    setup = {"CHEESE_ROOT_SNAPSHOT": "{}"} if restoring else {}
    work = session_work._start_executor(
        SimpleNamespace(exec=host),
        None,
        device_id="source",
        namespace=uuid.uuid4(),
        work_resource=str(uuid.uuid4()),
        setup=setup,
        sandbox=True,
        platform_machine=True,
    )
    if restoring:
        assert await work == {"ready": True}
    else:
        with pytest.raises(TimeoutError):
            await work


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
    async with client.test_factory() as db:
        home = await _source_home(db, room)
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
            await db.delete(await db.get(SandboxHome, home.id))
            await db.commit()
        async with client.test_factory() as db:
            restored = await root_snapshots.for_restore(db, room.session_id)

        def download():
            with urllib.request.urlopen(restored["url"]) as response:
                return response.read()

        assert await asyncio.to_thread(download) == body
        assert restored["image"].endswith("1" * 64)


async def test_an_oversized_root_does_not_offer_an_older_backup(client, monkeypatch):
    room = await _room(client, on_cloud=True)
    async with client.test_factory() as db:
        home = await _source_home(db, room)
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
