"""Remote reads leave the only pool connection available to another request."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import GatewayUnavailableError
from app.domain.project import forge
from app.domain.repository.forge_files import ProjectFiles
from tests.conftest import TEST_DATABASE_URL


@pytest.mark.anyio
@pytest.mark.parametrize("remote_error", [None, "timeout", "connection"])
async def test_forge_wait_releases_the_request_connection(
    _pg_schema, monkeypatch, remote_error
):
    engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    original_client = httpx.AsyncClient
    async with sessions() as session:

        async def binding(*args):
            await session.execute(text("SELECT 1"))
            return SimpleNamespace(api_url="https://forge.test", repo="owner/repo")

        async def check_available():
            assert not session.in_transaction()
            async with sessions() as other:
                assert await other.scalar(text("SELECT 1")) == 1

        async def token():
            await check_available()
            return "test-token", "later"

        async def remote(request):
            await check_available()
            if remote_error == "timeout":
                raise httpx.ReadTimeout("private host detail", request=request)
            if remote_error == "connection":
                raise httpx.ConnectError("private host detail", request=request)
            return httpx.Response(200, json={"files": []})

        monkeypatch.setattr(forge, "binding_for_project", binding)
        monkeypatch.setattr(
            forge,
            "tokens_for_project",
            AsyncMock(return_value=SimpleNamespace(installation_token=token)),
        )
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original_client(
                transport=httpx.MockTransport(remote), **kwargs
            ),
        )
        if remote_error:
            with pytest.raises(GatewayUnavailableError) as caught:
                await forge.repository_data(uuid.uuid4(), session, release_session=True)
            assert "private host detail" not in caught.value.message
        else:
            assert await forge.repository_data(
                uuid.uuid4(), session, release_session=True
            ) == {"files": []}
    await engine.dispose()


@pytest.mark.anyio
async def test_machine_wait_releases_the_request_connection(_pg_schema, monkeypatch):
    from app.domain.repository import forge_files

    engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    room_id, project_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session:
        await session.execute(text("SELECT 1"))
        task = SimpleNamespace(id=uuid.uuid4(), room_id=room_id)
        reader = ProjectFiles(session, project_id, task.id, release_session=True)
        monkeypatch.setattr(reader, "task", AsyncMock(return_value=task))
        monkeypatch.setattr(
            session,
            "get",
            AsyncMock(return_value=SimpleNamespace(id=room_id, resource_id=room_id)),
        )
        monkeypatch.setattr(
            forge_files.AgentSessionService,
            "places_in_room",
            AsyncMock(
                return_value=[
                    SimpleNamespace(resource_id=str(room_id), lease={"kind": "device"})
                ]
            ),
        )

        async def remote(*args):
            assert not session.in_transaction()
            async with sessions() as other:
                assert await other.scalar(text("SELECT 1")) == 1
            return {"files": []}

        monkeypatch.setattr(forge_files.execution, "call", remote)
        assert await reader.live("tree") == {"files": []}
    await engine.dispose()


@pytest.mark.anyio
async def test_forge_cancellation_keeps_the_single_connection_pool_reusable(
    _pg_schema, monkeypatch
):
    engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entered_remote = asyncio.Event()
    release_remote = asyncio.Event()
    remote_cancelled = asyncio.Event()
    original_client = httpx.AsyncClient
    repository_data_task = None

    try:
        async with sessions() as session:

            async def binding(*args):
                await session.execute(text("SELECT 1"))
                return SimpleNamespace(api_url="https://forge.test", repo="owner/repo")

            async def token():
                return "test-token", "later"

            async def blocked_remote(request):
                entered_remote.set()
                try:
                    await release_remote.wait()
                except asyncio.CancelledError:
                    remote_cancelled.set()
                    raise
                return httpx.Response(200, json={"files": []})

            monkeypatch.setattr(forge, "binding_for_project", binding)
            monkeypatch.setattr(
                forge,
                "tokens_for_project",
                AsyncMock(return_value=SimpleNamespace(installation_token=token)),
            )
            monkeypatch.setattr(
                httpx,
                "AsyncClient",
                lambda **kwargs: original_client(
                    transport=httpx.MockTransport(blocked_remote), **kwargs
                ),
            )

            repository_data_task = asyncio.create_task(
                forge.repository_data(uuid.uuid4(), session, release_session=True)
            )
            await asyncio.wait_for(entered_remote.wait(), timeout=2)

            async with sessions() as concurrent_session:
                assert await concurrent_session.scalar(text("SELECT 1")) == 1

            repository_data_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await repository_data_task
            await asyncio.wait_for(remote_cancelled.wait(), timeout=2)

            async with sessions() as post_cancel_session:
                assert await post_cancel_session.scalar(text("SELECT 1")) == 1
    finally:
        release_remote.set()
        if repository_data_task is not None and not repository_data_task.done():
            repository_data_task.cancel()
            await asyncio.gather(repository_data_task, return_exceptions=True)
        await engine.dispose()
