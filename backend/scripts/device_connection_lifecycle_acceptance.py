"""Exercise a real executor task across a killed backend RPC process.

This uses the production uvicorn owner, connector WebSocket protocol, and remote
executor runtime. Device token lookup is replaced to avoid a Postgres dependency,
and a Python connector harness relays frames in place of the packaged Go binary.
An independent owner checkout exercises wire compatibility with a released
version; dependencies and container entrypoints are outside this check.
"""

import argparse
import asyncio
import json
import multiprocessing
import os
import signal
import subprocess
import sys
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


def backend_waiter(results: multiprocessing.Queue, generation: int, port: int) -> None:
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
            return await backend.call_executor(
                "acceptance-machine",
                "/acceptance/.claude/executor",
                "invoke",
                {
                    "id": "same-executor-request",
                    "tool": "Bash",
                    "args": {
                        "command": (
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


def main() -> int:
    multiprocessing.set_start_method("spawn")
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-source", type=Path, default=root)
    parser.add_argument("--owner-revision")
    parser.add_argument("--claude", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18783)
    options = parser.parse_args()
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
