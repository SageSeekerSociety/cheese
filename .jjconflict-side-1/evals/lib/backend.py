"""Isolated eval backend: fresh sqlite DB + uvicorn on its own port.

Boots the REAL FastAPI app (app.main:app) as a subprocess with an eval-only
environment: per-run sqlite database, per-run workspace root (so the in-flight
turn registry, worktrees and session dirs never touch the dev backend), sandbox
disabled (turns run as plain model turns through the real gateway — see
README "沙箱模式" for the trade-off), scheduler off.

The gateway credentials (ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / AGENT_MODEL)
come from backend/.env exactly like the dev backend — pydantic-settings reads it
because the subprocess runs with cwd=backend. Explicit env vars set here override
.env values (env > .env in pydantic-settings).
"""

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
BOOTSTRAP_DB = Path(__file__).resolve().parent / "bootstrap_db.py"

DEFAULT_PORT = 8097


class EvalBackendError(RuntimeError):
    pass


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class EvalBackend:
    """Owns the eval backend subprocess for one runner invocation."""

    def __init__(self, run_dir: Path, *, port: int = DEFAULT_PORT) -> None:
        self.run_dir = run_dir
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.ws_base_url = f"ws://127.0.0.1:{port}"
        # Global sandbox token: lets the runner call cheese-gated write endpoints
        # (e.g. seeding project memory) against ITS OWN backend only.
        self.sandbox_token = secrets.token_hex(24)
        self.db_path = run_dir / "eval.db"
        self.workspace_root = run_dir / "workspaces"
        self.log_path = run_dir / "backend.log"
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_file = None

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                # 4 slashes = absolute sqlite path.
                "DATABASE_URL": f"sqlite+aiosqlite:///{self.db_path}",
                "AGENT_SANDBOX_ENABLED": "false",
                "WORKSPACE_ROOT": str(self.workspace_root),
                "SCHEDULER_INTERVAL_SECONDS": "0",
                "SANDBOX_TOKEN": self.sandbox_token,
                # Never inherit the dev process' request-scoped overrides.
                "PYTHONPATH": str(BACKEND_DIR),
            }
        )
        return env

    def bootstrap_db(self) -> None:
        """Create the schema on the fresh sqlite file (subprocess: settings must
        bind to the eval DATABASE_URL, not to this process' env)."""
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, str(BOOTSTRAP_DB)],
            cwd=BACKEND_DIR,
            env=self._env(),
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise EvalBackendError(
                "eval DB bootstrap failed:\n"
                + proc.stderr.decode(errors="replace")[-2000:]
            )

    def start(self, *, health_timeout_s: float = 60.0) -> None:
        if not _port_free(self.port):
            raise EvalBackendError(
                f"port {self.port} is busy — is another eval run (or something "
                f"else) using it? Pass --port to pick a free one."
            )
        self.bootstrap_db()
        self._log_file = open(self.log_path, "wb")  # noqa: SIM115 - lives with the proc
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=BACKEND_DIR,
            env=self._env(),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + health_timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise EvalBackendError(
                    f"eval backend exited early (code {self._proc.returncode}) — "
                    f"see {self.log_path}"
                )
            try:
                with urllib.request.urlopen(
                    f"{self.base_url}/health", timeout=2
                ) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        raise EvalBackendError(
            f"eval backend did not become healthy within {health_timeout_s}s — "
            f"see {self.log_path}"
        )

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
        self._proc = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
