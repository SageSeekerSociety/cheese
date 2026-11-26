"""configure-frontend-upstream.sh refuses a direct listen address nginx could not load."""

import pathlib
import subprocess
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "llm-tunnel" / "configure-frontend-upstream.sh"


def main() -> None:
    for address in ("127.0.0.1:65536", "127.0.0.1:0", "127.0.0.1:99999"):
        with tempfile.TemporaryDirectory() as active:
            result = subprocess.run(
                ["bash", str(SCRIPT), active, "18085", "8080", address],
                text=True,
                capture_output=True,
            )
            assert result.returncode != 0, (address, result.stdout)
            assert "Invalid listen address" in result.stderr, (address, result.stderr)
            assert not (pathlib.Path(active) / "frontend.conf").exists(), address
    print("llm tunnel listen validation: ok")


if __name__ == "__main__":
    main()
