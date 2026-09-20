"""Legacy raw archive ingress and portable device inventory.

Archived-room cleanup behavior is covered in test_archive_lifecycle.py.
"""

import hashlib
import io
import os
import shlex
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.domain.agent.device_provider import list_device_storage
from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.device.supply import Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.main import app as asgi_app

pytestmark = pytest.mark.anyio


async def _put(path: str, body: bytes, *, token: str | None) -> httpx.Response:
    """A request at the platform's own route, from inside the test's loop."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi_app), base_url="http://device"
    ) as http:
        return await http.put(path, content=body, headers=headers)


def _home_archive(line: str) -> bytes:
    """A home's `.claude/projects` and `.claude/todos`, packed the way the
    upload script's tar packs them."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in (
            (".claude/projects/-home-agent-work/session.jsonl", line),
            (".claude/todos/agent.json", "[]"),
        ):
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _stored_archives(project_id: uuid.UUID, place_id: uuid.UUID) -> list[Path]:
    place = Path(settings.transcripts_dir) / str(project_id) / str(place_id)
    return sorted(place.glob("*.tar.gz"))


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(settings, "workspace_root", str(root))
    return root


@pytest.fixture(autouse=True)
def transcripts_dir(tmp_path, monkeypatch) -> Path:
    """Where uploads land; every test here may store one."""
    root = tmp_path / "transcripts"
    monkeypatch.setattr(settings, "transcripts_dir", str(root))
    return root


async def _seed_device(
    session, device_id: str, *, project_id: uuid.UUID | None = None
) -> None:
    """An enrolled self-hosted machine, optionally one the project may run on."""
    owner = await IdentityService(session).ensure_agent_user()
    session.add(
        DeviceRow(
            device_id=device_id,
            name=device_id,
            token=f"tok-{device_id}",
            owner_user_id=owner.id,
            created_at=datetime.now(UTC),
        )
    )
    session.add(HostedDeviceRow(device_id=device_id, owner_user_id=owner.id))
    await session.flush()
    if project_id is not None:
        await sql_device_service(session).assign_to_project(
            device_id, project_id, actor_user_id=owner.id
        )


@pytest.mark.parametrize("roots", [(), ("home",), ("work",), ("home", "work")])
async def test_device_listing_uses_one_portable_shell_and_ignores_symlinks(
    tmp_path, roots
):
    project, place = str(uuid.uuid4()), str(uuid.uuid4())
    outside = tmp_path / "outside"
    (outside / place).mkdir(parents=True)
    for kind in roots:
        root = tmp_path / ".cheese" / kind
        (root / project / place).mkdir(parents=True)
        (root / str(uuid.uuid4())).symlink_to(outside, target_is_directory=True)
        (root / project / str(uuid.uuid4())).symlink_to(
            outside, target_is_directory=True
        )

    class ShellHub:
        calls = 0

        async def exec(self, device_id, command, *, timeout):
            self.calls += 1
            result = subprocess.run(
                [*command[:-1], f"HOME={shlex.quote(str(tmp_path))}; {command[-1]}"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit": result.returncode,
                "truncated": False,
            }

    hub = ShellHub()
    assert await list_device_storage("fixture", hub=hub) == [
        (kind, project, place) for kind in roots
    ]
    assert hub.calls == 1


async def test_the_upload_route_believes_the_pin_first_and_the_project_after(
    client, workspace_root
):
    """The machine a place is pinned to may store its transcripts and no other
    may, whatever else it serves; with no pin to go by, a machine the project
    may run on is believed. Nothing but a device token opens the route."""
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="钉住", created_by="u"
        )
        await _seed_device(session, "dev-ran-it")
        await _seed_device(session, "dev-other", project_id=project.id)
        await sql_device_service(session).bind_topic_device(
            topic.id, "dev-ran-it", Visibility.host
        )
        await session.commit()
        pid, tid = project.id, topic.id
    url = f"/connector/transcripts/{pid}/{tid}"
    body = _home_archive("{}")

    assert (await _put(url, body, token=None)).status_code == 401
    assert (await _put(url, body, token="tok-dev-other")).status_code == 403
    assert _stored_archives(pid, tid) == []

    stored = await _put(url, body, token="tok-dev-ran-it")
    assert stored.status_code == 200, stored.text
    assert stored.json()["size"] == len(body)
    assert stored.json()["sha256"] == hashlib.sha256(body).hexdigest()
    assert len(_stored_archives(pid, tid)) == 1
    # A second upload for the same place is a second file, never an overwrite.
    assert (await _put(url, body, token="tok-dev-ran-it")).status_code == 200
    assert len(_stored_archives(pid, tid)) == 2

    orphan = f"/connector/transcripts/{pid}/{uuid.uuid4()}"
    assert (await _put(orphan, body, token="tok-dev-other")).status_code == 200
    assert (await _put(orphan, body, token="tok-dev-ran-it")).status_code == 403
    nowhere = f"/connector/transcripts/{uuid.uuid4()}/{tid}"
    assert (await _put(nowhere, body, token="tok-dev-ran-it")).status_code == 404


async def test_the_upload_route_keeps_nothing_it_refuses(
    client, workspace_root, monkeypatch
):
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="太大", created_by="u"
        )
        await _seed_device(session, "dev-big", project_id=project.id)
        await session.commit()
        pid, tid = project.id, topic.id
    url = f"/connector/transcripts/{pid}/{tid}"
    place = Path(settings.transcripts_dir) / str(pid) / str(tid)

    monkeypatch.setattr(settings, "transcripts_max_bytes", 1024)
    # Random bytes, so gzip cannot shrink the body back under the cap.
    too_big = _home_archive(os.urandom(4096).hex())
    assert len(too_big) > 1024
    assert (await _put(url, too_big, token="tok-dev-big")).status_code == 413
    assert list(place.rglob("*")) == []

    monkeypatch.setattr(settings, "transcripts_max_bytes", 512 * 1024 * 1024)
    assert (await _put(url, b"not a tar.gz", token="tok-dev-big")).status_code == 400
    truncated = _home_archive("{}")[:-8]
    assert (await _put(url, truncated, token="tok-dev-big")).status_code == 400
    assert list(place.rglob("*")) == []
