"""The platform a personal-芝士 eval runs against: the real app, on a database
of its own, answering through the real model.

What is real: the FastAPI app in this process, served by uvicorn on a port of
its own; a Postgres database made for the run and migrated to head; a Valkey
database index of its own; pi, the pinned binary the tests use, started on this
machine the way the session host starts it (``tests.support.session_host``);
and the model, reached through an SSH tunnel to the dev gateway.

What is not: the gateway keys. The platform mints a key per person and one per
service (the authorization check's among them) with the gateway's admin key.
The eval never holds that key. It holds one key minted for it on the dev
gateway with a budget of its own, and every key the platform would mint is
that one; it also stands where the admin key would, for the reads an ordinary
key may make (``/model/info``, the rates). So the eval adds nothing to the dev
gateway's people and cannot revoke a key dev is using (a service key is minted
by deleting the one under the same alias first). Reading what a key spent
needs the admin key, so the platform's charges come out empty; the eval counts
cost from the tokens itself.

Settings are read when ``app`` is first imported, so ``prepare`` sets the
environment and only then imports it.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

#: Where the eval's own gateway key is kept, outside the repository.
KEY_FILE = Path.home() / ".config/cheese-eval/key"
#: The dev gateway, through ``ssh -L 14000:172.17.0.1:4000 cheese-dev``.
GATEWAY = os.environ.get("EVAL_GATEWAY", "http://127.0.0.1:14000")
TUNNEL = "ssh -f -N -L 14000:172.17.0.1:4000 cheese-dev"

PG = os.environ.get("EVAL_PG", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5994")
VALKEY = os.environ.get("EVAL_VALKEY", "redis://127.0.0.1:6994/9")
DEVICE = "center"


class NotReady(SystemExit):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def gateway_key() -> str:
    if not KEY_FILE.exists():
        raise NotReady(
            f"no eval gateway key at {KEY_FILE}; see evals/assistant/README.md"
        )
    key = KEY_FILE.read_text().strip()
    request = urllib.request.Request(
        f"{GATEWAY}/v1/models", headers={"Authorization": f"Bearer {key}"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(request, timeout=10).read()
    except (urllib.error.URLError, OSError) as e:
        raise NotReady(
            f"the dev gateway is not reachable at {GATEWAY} ({e}); run: {TUNNEL}"
        )
    return key


def _database(name: str) -> str:
    import asyncpg

    async def create() -> None:
        admin = await asyncpg.connect(PG.replace("+asyncpg", "") + "/postgres")
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()

    asyncio.run(create())
    url = f"{PG}/{name}"
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if migrated.returncode:
        raise NotReady(f"alembic upgrade failed:\n{migrated.stderr[-3000:]}")
    return url


def drop_database(name: str) -> None:
    import asyncpg

    async def drop() -> None:
        admin = await asyncpg.connect(PG.replace("+asyncpg", "") + "/postgres")
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()

    asyncio.run(drop())


@dataclass
class Platform:
    url: str
    database: str
    key: str
    model: str
    home: Path
    server: object

    def stop(self) -> None:
        self.server.should_exit = True  # type: ignore[attr-defined]


def prepare(run_dir: Path, *, database: str | None = None) -> Platform:
    """Make the database, set the environment, import the app and serve it."""
    key = gateway_key()
    database = database or f"cheese_eval_{time.strftime('%m%d_%H%M%S')}"
    url = _database(database)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    os.environ.update(
        {
            "DATABASE_URL": url,
            "REDIS_URL": VALKEY,
            "JWT_SECRET": secrets.token_hex(32),
            "LLM_GATEWAY_ADMIN_BASE": GATEWAY,
            # Not an admin key: the eval's own, for the reads any key may make.
            # Nothing is minted or revoked with it (``_one_key``).
            "LLM_GATEWAY_ADMIN_KEY": key,
            "ANTHROPIC_BASE_URL": GATEWAY,
            "AGENT_SESSION_API_BASE": base,
            "AGENT_SESSION_DEVICE_ID": DEVICE,
            "WORKSPACE_ROOT": str(run_dir / "workspaces"),
            "PLATFORM_ADMIN_HANDLES": '["eval-admin"]',
        }
    )
    os.environ.pop("ALL_PROXY", None)
    os.environ.pop("all_proxy", None)
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)

    from app.core.config import settings

    home = run_dir / "host"
    _one_key(key)
    _no_forge()
    _the_only_backend()
    _serve_the_host(home)
    server = _serve(port)
    return Platform(base, database, key, settings.assistant_model, home, server)


def _one_key(key: str) -> None:
    """Every key the platform would mint, a person's or a service's, is the
    eval's own: patched where it is minted and wherever ``service_key`` was
    imported by name."""
    import app.main  # noqa: F401  (every module that imports service_key)
    from app.domain import service_keys
    from app.domain.agent.gateway import LlmGateway

    async def person_key(self, user_id: int, model: str) -> str:
        return key

    async def service_key(session, spec, transport=None) -> str:
        return key

    async def project_key(self, *args, **kwargs) -> str:
        return key

    LlmGateway.mint_person_key = person_key  # type: ignore[method-assign]
    LlmGateway.mint_project_key = project_key  # type: ignore[method-assign]
    original = service_keys.service_key
    for module in list(sys.modules.values()):
        if getattr(module, "service_key", None) is original:
            module.service_key = service_key  # type: ignore[attr-defined]


def _the_only_backend() -> None:
    """The eval's backend owns the platform's standing work for the whole run.
    A backend holds that on a connection to its database; the eval drops and
    remakes the database before every task, which cuts that connection, and a
    backend that finds its hold gone shuts itself down for the one that took
    over. Here there is no other."""
    from app.core.ownership import Ownership

    async def still_held(self) -> bool:
        return True

    Ownership.still_held = still_held  # type: ignore[method-assign]


def _no_forge() -> None:
    """Projects get an inert repository binding and no repository: 芝士 is not
    asked to touch code here, and opening a task only reads the branch name."""
    from app.domain.project import forge
    from app.domain.project.models import ProjectForge
    from app.domain.repository import forge_files

    async def provision_repository(project_id, session, **kwargs):
        binding = await forge.binding_for_project(project_id, session)
        if binding is not None:
            return binding
        binding = ProjectForge(
            project_id=project_id,
            kind="forgejo",
            repo=f"project-{project_id.hex}/code",
            url=f"https://forge.invalid/project-{project_id.hex}/code.git",
            api_url="https://forge.invalid/api/v1",
            default_branch="main",
        )
        session.add(binding)
        await session.flush()
        return binding

    async def default_branch(project_id, session, **kwargs):
        return "main"

    async def nothing(*args, **kwargs):
        return None

    forge.provision_repository = provision_repository
    forge.default_branch = default_branch
    forge.ensure_author_email = nothing
    forge.repository_data = nothing
    forge_files.default_branch = default_branch
    forge_files.repository_data = nothing
    forge_files.branch_head = nothing


def _serve_the_host(home: Path) -> None:
    """The session host is this machine, as in the tests."""
    from app.api.deps import consumptions_for, get_consumptions, get_session_host
    from app.domain.agent.session_host.host import SessionHost
    from app.main import app
    from tests.support.session_host import Host, install_pi

    install_pi(home)
    people = SessionHost(Host(home))
    questions = consumptions_for(people, _chat_service())
    app.dependency_overrides[get_session_host] = lambda: people
    app.dependency_overrides[get_consumptions] = lambda: questions


def _chat_service():
    from app.api.deps import get_chat_service

    return get_chat_service()


def _serve(port: int):
    import uvicorn

    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise NotReady("the eval backend did not start; see the log above")
        time.sleep(0.1)
    return server
