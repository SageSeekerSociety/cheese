"""What the backend container runs: the realtime process, and the api pool
beside it when ``API_POOL_WORKERS`` asks for one (`core/process_role`).

Without a pool this becomes uvicorn itself (``exec``), exactly the command the
image always ran, so Docker starts and signals the server directly.

With a pool it stays as the container's first process and runs two servers:

- the realtime process on :8081 (``BACKEND_WORKERS`` of it, 1 everywhere), the
  backend as it always was;
- the pool on ``API_POOL_PORT`` (:8085), ``API_POOL_WORKERS`` uvicorn workers
  started with ``BACKEND_ROLE=api``, answering the plain reads app-router sends
  them (`deploy/llm-tunnel/app-router.conf`).

Signals go where they mean something: SIGTERM and SIGINT to both, SIGUSR1 —
"hand the running work over" (`main.lifespan`) — to the realtime process only,
which is the one holding it. When either server exits the other is stopped and
the container exits with it, so Docker's restart and the deploy's health check
see one backend, whole or down.
"""

import os
import signal
import subprocess
import sys
import time

COMMON = [
    "--host",
    "0.0.0.0",
    "--timeout-graceful-shutdown",
    "10",
    "--no-access-log",
]


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def realtime_argv() -> list[str]:
    workers = str(_int("BACKEND_WORKERS", 1))
    return ["uvicorn", "app.main:app", "--port", "8081", *COMMON, "--workers", workers]


def pool_argv(workers: int) -> list[str]:
    port = str(_int("API_POOL_PORT", 8085))
    return [
        "uvicorn",
        "app.main:app",
        "--port",
        port,
        *COMMON,
        "--workers",
        str(workers),
    ]


def pool_env(workers: int) -> dict[str, str]:
    """The pool's own role and its own share of the database.

    Each pool worker keeps a pool of connections; `BACKEND_WORKERS` is how the
    app divides a budget between workers (`Settings.db_connections_per_worker`),
    so the pool is given its own budget and its own worker count. By default
    four connections and two more under load per worker: a read holds one for
    milliseconds, and the server's total has to fit the realtime process, the
    owners and a deploy's second slot as well (`test_db_pool_fits_the_server`).
    """
    env = dict(os.environ)
    env["BACKEND_ROLE"] = "api"
    env["BACKEND_WORKERS"] = str(workers)
    env["DB_POOL_SIZE"] = str(_int("API_POOL_DB_POOL_SIZE", 4 * workers))
    env["DB_MAX_OVERFLOW"] = str(_int("API_POOL_DB_MAX_OVERFLOW", 2 * workers))
    return env


def main() -> int:
    workers = _int("API_POOL_WORKERS", 0)
    if workers <= 0:
        argv = realtime_argv()
        os.execvp(argv[0], argv)

    realtime_env = dict(os.environ)
    realtime_env["BACKEND_ROLE"] = "realtime"
    realtime = subprocess.Popen(realtime_argv(), env=realtime_env)
    pool = subprocess.Popen(pool_argv(workers), env=pool_env(workers))
    children = (realtime, pool)

    def forward(signum, _frame) -> None:
        targets = (realtime,) if signum == signal.SIGUSR1 else children
        for child in targets:
            if child.poll() is None:
                child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(signum, forward)

    while all(child.poll() is None for child in children):
        time.sleep(0.5)
    exited = next(child for child in children if child.poll() is not None)
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=70)
        except subprocess.TimeoutExpired:
            child.kill()
    return exited.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
