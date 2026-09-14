"""Exercise the connection owner across a killed backend RPC process.

This uses a real uvicorn server and WebSocket. The only fake is device token
lookup, which keeps the acceptance independent from Postgres while retaining the
production connector route and wire protocol.
"""

import asyncio
import base64
import json
import multiprocessing
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PORT = 18783
SECRET = "lifecycle-acceptance-secret"
TRACE_ID = "execution-lifecycle-acceptance"


def owner() -> None:
    os.environ["DEVICE_CONNECTION_URL"] = ""
    os.environ["DEVICE_CONNECTION_SECRET"] = SECRET
    os.environ["DEVICE_CONNECTION_OWNER"] = "1"
    from app.api.routes.connector import get_device_service
    from app.core.db import get_db
    from app.device_connection_app import app

    class Service:
        async def verify_token(self, _: str):
            return SimpleNamespace(device_id="acceptance-machine", name="acceptance")

    class Session:
        async def commit(self) -> None:
            pass

    async def session():
        yield Session()

    app.dependency_overrides[get_device_service] = Service
    app.dependency_overrides[get_db] = session
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


async def connector(events: multiprocessing.Queue) -> None:
    async with websockets.connect(
        f"ws://127.0.0.1:{PORT}/connector/agent?token=acceptance"
    ) as socket:
        welcome = json.loads(await socket.recv())
        events.put({"event": "connected", "welcome": welcome})
        await socket.send(json.dumps({"t": "hello", "v": 3, "executor": True}))
        message = json.loads(await socket.recv())
        events.put({"event": "execution_received", "id": message["id"]})
        await asyncio.sleep(4)
        encoded = json.dumps({"result": {"answer": "owner-retained-result"}}).encode()
        await socket.send(
            json.dumps(
                {
                    "t": "execution.data",
                    "id": message["id"],
                    "data": base64.b64encode(encoded).decode(),
                }
            )
        )
        await socket.send(
            json.dumps({"t": "execution.result", "id": message["id"], "error": ""})
        )
        events.put({"event": "execution_completed", "id": message["id"]})
        await asyncio.sleep(2)


def connector_process(events: multiprocessing.Queue) -> None:
    asyncio.run(connector(events))


def backend_waiter(results: multiprocessing.Queue, generation: int) -> None:
    response = httpx.post(
        f"http://127.0.0.1:{PORT}/internal/device-connection/call/call_executor",
        headers={"X-Device-Connection-Secret": SECRET},
        json={
            "device_id": "acceptance-machine",
            "state": "/acceptance/.claude/executor",
            "method": "control",
            "params": {"request_id": "same-control-request"},
            "timeout": 30,
            "trace_id": TRACE_ID,
        },
        timeout=35,
    )
    results.put(
        {
            "generation": generation,
            "status": response.status_code,
            "body": response.json(),
        }
    )


def wait_for_owner() -> None:
    for _ in range(100):
        try:
            if (
                httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=0.2).status_code
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
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"device-connection-acceptance-{stamp}.jsonl"
    events: multiprocessing.Queue = multiprocessing.Queue()
    results: multiprocessing.Queue = multiprocessing.Queue()
    owner_process = multiprocessing.Process(target=owner, name="connection-owner")
    connector_worker = multiprocessing.Process(
        target=connector_process, args=(events,), name="device-connector"
    )
    first_backend = multiprocessing.Process(
        target=backend_waiter, args=(results, 1), name="backend-generation-1"
    )
    second_backend = multiprocessing.Process(
        target=backend_waiter, args=(results, 2), name="backend-generation-2"
    )
    with log_path.open("x", encoding="utf-8") as log:
        try:
            owner_process.start()
            wait_for_owner()
            owner_pid = owner_process.pid
            record(log, "owner_ready", owner_pid=owner_pid, port=PORT)

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
            record(log, **completed)
            record(
                log,
                "replacement_received",
                owner_pid=owner_process.pid,
                owner_unchanged=owner_process.pid == owner_pid,
                **result,
            )
            if result["body"] != {"result": {"answer": "owner-retained-result"}}:
                raise RuntimeError(f"replacement received wrong result: {result}")
            if received["id"] != completed["id"] or received["id"] != TRACE_ID:
                raise RuntimeError("device received or completed a different execution")
            if not owner_process.is_alive() or owner_process.pid != owner_pid:
                raise RuntimeError(
                    "connection owner changed during backend replacement"
                )
            record(log, "acceptance_passed", owner_pid=owner_pid)
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
    print(log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
