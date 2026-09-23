"""Exercise a real executor task across a killed backend RPC process.

Image mode runs the published owner's deployed command and packaged Go connector
against an isolated migrated database. Source mode keeps a faster protocol check:
it replaces device authentication and uses a Python connector harness.
"""

import argparse
import asyncio
import hashlib
import json
import multiprocessing
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The connector half below speaks the same executor frames the doubles under
# tests/ speak, and they are declared in one place so a wire change reaches
# all of them (backend/tests/fixtures/wire pins that place against the Go side).
from tests.support import wire  # noqa: E402

SECRET = "lifecycle-acceptance-secret"
TRACE_ID = "execution-lifecycle-acceptance"


def owner(source: str, port: int) -> None:
    sys.path.insert(0, str(Path(source) / "backend"))
    os.environ["DEVICE_CONNECTION_URL"] = ""
    os.environ["DEVICE_CONNECTION_SECRET"] = SECRET
    os.environ["DEVICE_CONNECTION_OWNER"] = "1"
    from app import device_connection_app
    from app.core.db import get_db
    from app.domain.device import owner_reads

    assert Path(device_connection_app.__file__).resolve().is_relative_to(Path(source))
    app = device_connection_app.app

    async def device_for_token(_session, _token):
        return SimpleNamespace(device_id="acceptance-machine", name="acceptance")

    class Session:
        async def commit(self) -> None:
            pass

    async def session():
        yield Session()

    owner_reads.device_for_token = device_for_token
    app.dependency_overrides[get_db] = session
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


async def connector(
    events: multiprocessing.Queue, task_root: str, claude: str, port: int
) -> None:
    from app.domain.agent.harness.claude_code.remote_execution import runtime

    workspace = Path(task_root)
    state = workspace / "executor"
    state.mkdir(parents=True)
    configuration = {
        "workspace": str(workspace),
        "env": {},
        "private": True,
        "claude": claude,
    }
    subprocess.run(
        [sys.executable, str(Path(runtime.__file__)), "start", "--state", str(state)],
        input=json.dumps(configuration),
        text=True,
        check=True,
        capture_output=True,
    )
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/connector/agent?token=acceptance"
    ) as socket:
        welcome = json.loads(await socket.recv())
        events.put({"event": "connected", "welcome": welcome})
        await socket.send(json.dumps({"t": "hello", "v": 3, "executor": True}))
        call = wire.ExecutionCall.parse(json.loads(await socket.recv()))
        events.put({"event": "execution_received", "id": call.id})
        request = call.request
        result = await asyncio.to_thread(
            runtime.request, state, request["method"], request["params"]
        )
        encoded = json.dumps({"result": result}).encode()
        await socket.send(json.dumps(wire.execution_data(call.id, encoded)))
        await socket.send(json.dumps(wire.execution_result(call.id)))
        events.put(
            {
                "event": "execution_completed",
                "id": call.id,
                "executor_result": result,
            }
        )
    runtime.request(state, "ping")
    subprocess.run(
        [sys.executable, str(Path(runtime.__file__)), "stop", "--state", str(state)],
        check=True,
    )
    events.put({"event": "executor_runtime_stopped"})


def connector_process(
    events: multiprocessing.Queue, task_root: str, claude: str, port: int
) -> None:
    asyncio.run(connector(events, task_root, claude, port))


def backend_waiter(
    results: multiprocessing.Queue,
    generation: int,
    port: int,
    state: str = "/acceptance/.claude/executor",
    command: str | None = None,
) -> None:
    from app.domain.agent.device_hub_rpc import RemoteDeviceHub

    async def collect():
        backend = RemoteDeviceHub(f"http://127.0.0.1:{port}", SECRET)
        try:
            await backend.start()
            assert backend.is_online("acceptance-machine")
            if generation == 1:
                await backend.adopt_screen(
                    "acceptance-machine",
                    "surviving-screen",
                    token="screen-token",
                    agent_user_id=42,
                    agent_handle="acceptance-agent",
                    execution_target={"home": "/acceptance"},
                )
            else:
                screen = backend.screen("surviving-screen")
                assert screen is not None
                assert screen.agent_handle == "acceptance-agent"
                assert screen.execution_target == {"home": "/acceptance"}
                try:
                    await backend._call_owner("unrecognised-method", {})
                except httpx.HTTPStatusError as exc:
                    assert exc.response.status_code == 404
                else:
                    raise AssertionError("owner accepted an unknown RPC method")
                assert backend.is_online("acceptance-machine")
                retained = backend.screen("surviving-screen")
                assert retained is not None
                assert retained.agent_handle == screen.agent_handle
                assert retained.execution_target == screen.execution_target
            return await backend.call_executor(
                "acceptance-machine",
                state,
                "invoke",
                {
                    "id": "same-executor-request",
                    "tool": "Bash",
                    "args": {
                        "command": command
                        or (
                            "sleep 4; printf 'run\\n' >> execution-count; "
                            "printf owner-retained-result"
                        ),
                    },
                },
                timeout=30,
                trace_id=TRACE_ID,
            )
        finally:
            await backend.close()

    result = asyncio.run(collect())
    results.put(
        {
            "generation": generation,
            "body": {"result": result},
        }
    )


def wait_for_owner(port: int) -> None:
    for _ in range(100):
        try:
            if (
                httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=0.2).status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("connection owner did not start")


def record(log, event: str, **values) -> None:
    row = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "commit": os.environ.get("ACCEPTANCE_COMMIT", "working-tree"),
        **values,
    }
    log.write(json.dumps(row, sort_keys=True) + "\n")
    log.flush()


def image_acceptance(options, root: Path) -> int:
    """Run the released owner and its packaged Go connector without code mounts."""
    os.environ["SANDBOX_TOKEN"] = SECRET
    from app.core.sandbox_auth import mint_scoped_token
    from app.domain.agent.harness.claude_code.remote_execution import runtime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    work = root / "tmp" / f"owner-image-{stamp}"
    work.mkdir(parents=True)
    state = work / "executor"
    state.mkdir()
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"device-connection-acceptance-image-{stamp}.jsonl"
    network = f"cheese-owner-{stamp.lower()}"
    resource_label = "cheese.owner-acceptance=" + "-".join(
        os.environ.get(key, fallback)
        for key, fallback in (
            ("GITHUB_RUN_ID", network),
            ("GITHUB_RUN_ATTEMPT", "local"),
            ("GITHUB_JOB", "acceptance"),
        )
    )
    database, owner_name, migration = (
        f"{network}-{suffix}" for suffix in ("db", "owner", "migrate")
    )
    containers = [migration, owner_name, database]
    children = []
    connector_worker = None
    runtime_started = False
    headers = {"X-Device-Connection-Secret": SECRET}
    url = f"http://127.0.0.1:{options.port}"

    def docker(*args, check=True, timeout=120, input=None):
        result = subprocess.run(
            ["docker", *args],
            input=input,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if check and result.returncode:
            raise RuntimeError(
                f"docker {args[0]} failed: {result.stderr}\n{result.stdout}"
            )
        return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()

    env = {
        "DATABASE_URL": f"postgresql+asyncpg://postgres:postgres@{database}:5432/cheese",
        "DEVICE_CONNECTION_URL": "",
        "DEVICE_CONNECTION_OWNER": "1",
        "DEVICE_CONNECTION_SECRET": SECRET,
        "DEPLOYED_VIA_COMPOSE": "1",
        "JWT_SECRET": "isolated-owner-acceptance-jwt-secret",
        "SANDBOX_TOKEN": SECRET,
        "PLATFORM_ADMIN_HANDLES": '["acceptance"]',
        "DB_POOL_SIZE": "5",
        "DB_MAX_OVERFLOW": "5",
    }
    env_args = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
    with (
        log_path.open("x") as log,
        tempfile.TemporaryDirectory(prefix="owner-tmux-") as tmux_tmp,
    ):
        try:
            record(
                log,
                "inputs",
                owner_image=options.owner_image,
                owner_revision=options.owner_revision,
                port=options.port,
                resource_label=resource_label,
            )
            started = time.monotonic()
            record(
                log,
                "image_pull_started",
                cached=bool(
                    docker(
                        "image",
                        "inspect",
                        "--format",
                        "{{.Id}}",
                        options.owner_image,
                        check=False,
                    )
                ),
            )
            docker(
                "pull", "--platform", "linux/amd64", options.owner_image, timeout=300
            )
            record(log, "image_pull_finished", elapsed_s=time.monotonic() - started)
            docker("network", "create", "--label", resource_label, network)
            docker(
                "run",
                "-d",
                "--name",
                database,
                "--label",
                resource_label,
                "--network",
                network,
                "-p",
                "127.0.0.1::5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=1g",
                "-e",
                "POSTGRES_PASSWORD=postgres",
                "-e",
                "POSTGRES_DB=cheese",
                "mirror.gcr.io/paradedb/paradedb:v0.18.8-pg16@sha256:8a14fee5257f554a60d70afc89490a6460a9833c3f7f99f7d88dbbf12e4042a2",
            )
            for _ in range(60):
                if "accepting connections" in docker(
                    "exec", database, "pg_isready", "-U", "postgres", check=False
                ):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("isolated Postgres did not start")
            started = time.monotonic()
            record(log, "released_migration_started")
            migration_output = docker(
                "run",
                "--name",
                migration,
                "--label",
                resource_label,
                "--network",
                network,
                *env_args,
                options.owner_image,
                "alembic",
                "upgrade",
                "head",
            )
            (work / "migration.log").write_text(migration_output)
            version_query = (
                "exec",
                database,
                "psql",
                "-U",
                "postgres",
                "-d",
                "cheese",
                "-At",
                "-c",
                "SELECT version_num FROM alembic_version ORDER BY version_num",
            )
            released_schema = docker(*version_query).splitlines()
            record(
                log,
                "released_migration_finished",
                heads=released_schema,
                elapsed_s=time.monotonic() - started,
            )
            database_port = docker("port", database, "5432/tcp").rsplit(":", 1)[1]
            started = time.monotonic()
            record(log, "current_migration_started")
            upgraded = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=root / "backend",
                env={
                    **os.environ,
                    **env,
                    "DATABASE_URL": f"postgresql+asyncpg://postgres:postgres@127.0.0.1:{database_port}/cheese",
                },
                text=True,
                capture_output=True,
                timeout=120,
            )
            (work / "current-migration.log").write_text(
                upgraded.stdout + upgraded.stderr
            )
            upgraded.check_returncode()
            current_schema = docker(*version_query).splitlines()
            record(
                log,
                "schema_upgraded",
                released_heads=released_schema,
                current_heads=current_schema,
                elapsed_s=time.monotonic() - started,
            )
            # The deployed owner command does not migrate. Keep the upgraded
            # schema while the old image performs its real device-token query.
            docker(
                "exec",
                "-i",
                database,
                "psql",
                "-U",
                "postgres",
                "-d",
                "cheese",
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "lease="
                + json.dumps(
                    {
                        "kind": "device",
                        "device_id": "acceptance-machine",
                        "resource_id": "22222222-2222-2222-2222-222222222222",
                        "home": str(work),
                        "state": str(state),
                    }
                ),
                input="""
INSERT INTO "user" (id,username,email,created_at,updated_at)
VALUES (42,'acceptance','acceptance@example.test',now(),now());
INSERT INTO device (device_id,name,token,owner_user_id,created_at)
VALUES ('acceptance-machine','acceptance','acceptance',42,now());
INSERT INTO projects
 (id,name,owner_handle,ai_mode,summary,settings,created_at,updated_at)
VALUES ('11111111-1111-1111-1111-111111111111','Acceptance','acceptance',
        'off','','{}',now(),now());
INSERT INTO topics (id,project_id,title,kind,status,is_private,created_at,updated_at)
VALUES ('22222222-2222-2222-2222-222222222222','11111111-1111-1111-1111-111111111111',
        'Acceptance','room','active',false,now(),now());
INSERT INTO agent_sessions
 (id,topic_id,agent_handle,harness,runtime_location,work_lease,created_at,updated_at)
VALUES ('33333333-3333-3333-3333-333333333333','22222222-2222-2222-2222-222222222222',
        'acceptance-agent','claude-code',:'lease'::json,:'lease'::json,now(),now());
""",
            )
            started = time.monotonic()
            record(log, "owner_starting")
            docker(
                "run",
                "-d",
                "--init",
                "--name",
                owner_name,
                "--label",
                resource_label,
                "--network",
                network,
                "-p",
                f"127.0.0.1:{options.port}:8082",
                *env_args,
                options.owner_image,
                "uvicorn",
                "app.device_connection_app:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8082",
            )
            owner_identity = json.loads(docker("inspect", owner_name))[0]
            image_env = owner_identity["Config"]["Env"]
            actual_version = next(
                item.split("=", 1)[1]
                for item in image_env
                if item.startswith("APP_VERSION=")
            )
            assert actual_version == options.owner_revision, (
                actual_version,
                options.owner_revision,
            )
            binary = work / "cheesehost"
            docker(
                "cp",
                f"{owner_name}:/app/connector-dist/linux-amd64/cheesehost",
                str(binary),
            )
            binary.chmod(0o755)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            version = subprocess.check_output(
                [str(binary), "--version"], text=True
            ).strip()
            record(
                log,
                "released_artifacts",
                image_id=owner_identity["Image"],
                owner_id=owner_identity["Id"],
                owner_version=actual_version,
                connector_sha256=digest,
                connector_version=version,
            )
            wait_for_owner(options.port)
            record(log, "owner_ready", elapsed_s=time.monotonic() - started)
            assert (
                httpx.get(
                    url + "/internal/device-connection/snapshot",
                    headers={"X-Device-Connection-Secret": "wrong"},
                ).status_code
                == 403
            )

            async def reject_device():
                try:
                    async with websockets.connect(
                        f"ws://127.0.0.1:{options.port}/connector/agent?token=wrong"
                    ):
                        raise AssertionError("owner accepted an invalid device token")
                except websockets.exceptions.InvalidStatus as exc:
                    assert exc.response.status_code == 403

            asyncio.run(reject_device())
            record(
                log, "authentication_rejected", internal_rpc=403, device_websocket=403
            )
            subprocess.run(
                [sys.executable, runtime.__file__, "start", "--state", str(state)],
                input=json.dumps(
                    {
                        "workspace": str(work),
                        "env": {},
                        "private": True,
                        "claude": str(options.claude.resolve()),
                    }
                ),
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )
            runtime_started = True
            config = work / "connector.json"
            config.write_text(
                json.dumps(
                    {
                        "base": url,
                        "token": "acceptance",
                        "device_id": "acceptance-machine",
                        "ws": f"ws://127.0.0.1:{options.port}/connector/agent",
                    }
                )
            )
            with (work / "connector.log").open("w") as connector_log:
                connector_worker = subprocess.Popen(
                    [str(binary), "run", "--config", str(config)],
                    stdout=connector_log,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "TMPDIR": tmux_tmp},
                )
            for _ in range(100):
                snapshot = httpx.get(
                    url + "/internal/device-connection/snapshot", headers=headers
                ).json()
                if any(
                    d["device_id"] == "acceptance-machine" and d["online"]
                    for d in snapshot["devices"]
                ):
                    break
                assert connector_worker.poll() is None, "Go connector exited"
                time.sleep(0.1)
            else:
                raise RuntimeError("Go connector did not attach")
            started = time.monotonic()
            record(log, "roundtrip_started")
            topic = "22222222-2222-2222-2222-222222222222"
            endpoint = f"{url}/topics/{topic}/execution/{topic}"
            token = mint_scoped_token(
                project_id="11111111-1111-1111-1111-111111111111",
                topic_id=topic,
                resource_id=topic,
            )
            wrong_project = mint_scoped_token(
                project_id="44444444-4444-4444-4444-444444444444",
                topic_id=topic,
                resource_id=topic,
            )
            request = {
                "method": "invoke",
                "params": {
                    "id": "endpoint-authenticated-call",
                    "tool": "Bash",
                    "args": {"command": "printf endpoint-result"},
                },
            }
            assert httpx.post(endpoint, json=request).status_code == 401
            assert (
                httpx.post(
                    endpoint, json=request, headers={"X-Cheese-Token": wrong_project}
                ).status_code
                == 403
            )
            response = httpx.post(
                endpoint, json=request, headers={"X-Cheese-Token": token}, timeout=30
            )
            response.raise_for_status()
            assert response.json()["value"]["stdout"] == "endpoint-result"
            record(
                log,
                "execution_endpoint_passed",
                missing_credential=401,
                wrong_project=403,
                authenticated_call=200,
            )
            # The sentinel proves Bash started before the HTTP waiter is killed.
            command = (
                f"printf started > {shlex.quote(str(work / 'started'))}; sleep 4; "
                "printf 'run\\n' >> execution-count; printf owner-retained-result"
            )
            results: multiprocessing.Queue = multiprocessing.Queue()
            for generation in (1, 2):
                child = multiprocessing.Process(
                    target=backend_waiter,
                    args=(results, generation, options.port, str(state), command),
                )
                children.append(child)
                child.start()
                if generation == 1:
                    for _ in range(200):
                        if (work / "started").exists():
                            break
                        assert child.is_alive(), (
                            "first backend exited before Bash started"
                        )
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("Bash did not start")
                    child.kill()
                    child.join(timeout=5)
                    record(
                        log, "backend_stopped", generation=1, exit_code=child.exitcode
                    )
            result = results.get(timeout=30)
            children[-1].join(timeout=5)
            value = result["body"]["result"]["value"]
            assert result["generation"] == 2 and children[-1].exitcode == 0
            assert value["stdout"] == "owner-retained-result" and value["stderr"] == ""
            assert value["interrupted"] is False
            assert (work / "execution-count").read_text().splitlines() == ["run"]
            after = json.loads(docker("inspect", owner_name))[0]
            assert after["Id"] == owner_identity["Id"] and after["State"]["Running"]
            assert after["State"]["StartedAt"] == owner_identity["State"]["StartedAt"]
            assert after["RestartCount"] == 0 and connector_worker.poll() is None
            assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest
            assert docker(*version_query).splitlines() == current_schema
            record(
                log,
                "acceptance_passed",
                execution_count=1,
                session_restored=True,
                owner_id=after["Id"],
                connector_pid=connector_worker.pid,
                elapsed_s=time.monotonic() - started,
                **result,
            )
        except BaseException as exc:
            record(log, "acceptance_failed", error=repr(exc))
            raise
        finally:
            for child in children:
                if child.is_alive():
                    child.kill()
                child.join(timeout=5)
            if connector_worker is not None:
                connector_worker.terminate()
                try:
                    connector_worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    connector_worker.kill()
                    connector_worker.wait(timeout=5)
            try:
                if runtime_started:
                    subprocess.run(
                        [
                            sys.executable,
                            runtime.__file__,
                            "stop",
                            "--state",
                            str(state),
                        ],
                        timeout=15,
                        check=False,
                    )
            finally:
                for name in containers:
                    (work / f"{name}.log").write_text(docker("logs", name, check=False))
                    docker("rm", "-f", name, check=False)
                docker("network", "rm", network, check=False)
    print(log_path)
    return 0


def main() -> int:
    multiprocessing.set_start_method("spawn")
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-source", type=Path, default=root)
    parser.add_argument("--owner-revision")
    parser.add_argument("--claude", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18783)
    parser.add_argument(
        "--owner-image", help="Published backend image pinned by digest"
    )
    options = parser.parse_args()
    if options.owner_image:
        if not options.owner_revision or "@sha256:" not in options.owner_image:
            parser.error(
                "image mode requires --owner-revision and an owner image digest"
            )
        return image_acceptance(options, root)
    owner_source = options.owner_source.resolve()
    owner_revision = subprocess.check_output(
        ["git", "-C", str(owner_source), "rev-parse", "HEAD"], text=True
    ).strip()
    if options.owner_revision and owner_revision != options.owner_revision:
        raise RuntimeError(
            f"owner revision is {owner_revision}, expected {options.owner_revision}"
        )
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"device-connection-acceptance-{stamp}.jsonl"
    events: multiprocessing.Queue = multiprocessing.Queue()
    results: multiprocessing.Queue = multiprocessing.Queue()
    task_root = root / "tmp" / f"device-connection-acceptance-{stamp}"
    task_root.mkdir(parents=True)
    owner_process = multiprocessing.Process(
        target=owner, args=(str(owner_source), options.port), name="connection-owner"
    )
    connector_worker = multiprocessing.Process(
        target=connector_process,
        args=(events, str(task_root), str(options.claude.resolve()), options.port),
        name="device-connector",
    )
    first_backend = multiprocessing.Process(
        target=backend_waiter,
        args=(results, 1, options.port),
        name="backend-generation-1",
    )
    second_backend = multiprocessing.Process(
        target=backend_waiter,
        args=(results, 2, options.port),
        name="backend-generation-2",
    )
    with log_path.open("x", encoding="utf-8") as log:
        try:
            record(
                log,
                "inputs",
                owner_revision=owner_revision,
                owner_source=str(owner_source),
                port=options.port,
            )
            owner_process.start()
            wait_for_owner(options.port)
            refused = httpx.get(
                f"http://127.0.0.1:{options.port}/internal/device-connection/snapshot",
                headers={"X-Device-Connection-Secret": "wrong-secret"},
                timeout=5,
            )
            assert refused.status_code == 403
            unknown = httpx.post(
                f"http://127.0.0.1:{options.port}/internal/device-connection/call/unrecognised-method",
                headers={"X-Device-Connection-Secret": SECRET},
                json={},
                timeout=5,
            )
            assert unknown.status_code == 404
            owner_pid = owner_process.pid
            record(log, "owner_ready", owner_pid=owner_pid, port=options.port)

            connector_worker.start()
            connected = events.get(timeout=10)
            record(log, **connected, owner_pid=owner_pid)

            first_backend.start()
            received = events.get(timeout=10)
            record(log, **received, backend_pid=first_backend.pid)
            assert first_backend.pid is not None
            os.kill(first_backend.pid, signal.SIGTERM)
            first_backend.join(timeout=5)
            record(
                log,
                "backend_stopped",
                generation=1,
                backend_pid=first_backend.pid,
                exit_code=first_backend.exitcode,
                owner_pid=owner_process.pid,
                owner_alive=owner_process.is_alive(),
            )

            second_backend.start()
            result = results.get(timeout=20)
            second_backend.join(timeout=5)
            completed = events.get(timeout=5)
            runtime_stopped = events.get(timeout=5)
            record(log, **completed)
            record(log, **runtime_stopped)
            record(
                log,
                "replacement_received",
                owner_pid=owner_process.pid,
                owner_unchanged=owner_process.pid == owner_pid,
                **result,
            )
            value = (result["body"]["result"] or {}).get("value", {})
            if (
                value.get("stdout") != "owner-retained-result"
                or value.get("stderr") != ""
                or value.get("interrupted") is not False
            ):
                raise RuntimeError(f"replacement received wrong result: {result}")
            executions = (task_root / "execution-count").read_text().splitlines()
            if executions != ["run"]:
                raise RuntimeError(f"executor task ran {len(executions)} times")
            if received["id"] != completed["id"] or received["id"] != TRACE_ID:
                raise RuntimeError("device received or completed a different execution")
            if not owner_process.is_alive() or owner_process.pid != owner_pid:
                raise RuntimeError(
                    "connection owner changed during backend replacement"
                )
            record(
                log,
                "acceptance_passed",
                owner_pid=owner_pid,
                executor_stdout="owner-retained-result",
                execution_count=len(executions),
            )
        except BaseException as exc:
            record(log, "acceptance_failed", error=repr(exc))
            raise
        finally:
            for process in (
                first_backend,
                second_backend,
                connector_worker,
                owner_process,
            ):
                if process.pid is None:
                    continue
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            from app.domain.agent.harness.claude_code.remote_execution import runtime

            subprocess.run(
                [
                    sys.executable,
                    runtime.__file__,
                    "stop",
                    "--state",
                    str(task_root / "executor"),
                ],
                check=True,
                timeout=15,
            )
    print(log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
