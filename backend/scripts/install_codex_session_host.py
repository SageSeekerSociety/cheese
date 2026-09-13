"""Install the pinned Codex binary into the central machine's Cheese directory."""

import fcntl
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

VERSION = "0.154.0"


def main() -> None:
    prefix = Path.home() / ".cheese/tools/codex"
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = prefix / "node_modules/.bin/codex"
    with (prefix / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.exists():
            log_path = prefix / (
                datetime.now(UTC).strftime("install-%Y%m%dT%H%M%SZ.log")
            )
            print(json.dumps({"install_log": str(log_path)}), flush=True)
            with log_path.open("w") as log:
                subprocess.run(
                    [
                        "npm",
                        "install",
                        "--prefix",
                        str(prefix),
                        "--save-exact",
                        "--no-audit",
                        "--no-fund",
                        f"@openai/codex@{VERSION}",
                    ],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
        version = subprocess.check_output([str(binary), "--version"], text=True).strip()
        if version != f"codex-cli {VERSION}":
            raise RuntimeError(f"Expected codex-cli {VERSION}, found {version}")
        print(json.dumps({"binary": str(binary), "version": version}))


if __name__ == "__main__":
    main()
