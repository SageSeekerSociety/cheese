"""Prepare native policy and feature caches before a cloud room is assigned."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FILES = ("remote-settings.json", "policy-limits.json")
FEATURE_FIELDS = ("cachedGrowthBookFeatures", "cachedGrowthBookFeaturesAt")
MAX_AGE = 3600


def build_startup_cache_prepare(version: str) -> str:
    source = Path(__file__).read_text()
    return (
        f'python3 - prepare "$HOME" "{version}" <<\'CHEESE_NATIVE_CACHE\'\n'
        f"{source}\nCHEESE_NATIVE_CACHE\n"
    )


def identity(owner: Path) -> tuple[dict, str]:
    env = json.loads((owner / ".claude/settings.json").read_text())["env"]
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise ValueError("Machine OAuth credential is missing")
    fingerprint = hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()
    return env, fingerprint


def prepare(owner: Path, version: str) -> None:
    env, fingerprint = identity(owner)
    directory = owner / ".cheese"
    directory.mkdir(exist_ok=True)
    bundle_path = directory / "native-startup-cache.json"
    # The private home contains no room files, prompts, or user hooks.
    with tempfile.TemporaryDirectory(prefix="native-init-", dir=directory) as tmp:
        home = Path(tmp)
        config = home / ".claude"
        config.mkdir()
        (config / "settings.json").write_text(json.dumps({"env": env}))
        (config / ".claude.json").write_text(
            json.dumps({"hasCompletedOnboarding": True})
        )
        binary = owner / ".cheese/claude/versions" / version
        with (directory / "native-startup-init.log").open("ab") as log:
            log.write(f"{time.time()} initializing {version}\n".encode())
            log.flush()
            subprocess.run(
                [str(binary), "--init-only"],
                cwd=home,
                env={
                    **os.environ,
                    **env,
                    "HOME": str(home),
                    "CLAUDE_CONFIG_DIR": str(config),
                    "DISABLE_AUTOUPDATER": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                timeout=45,
                check=True,
            )
        native_state = json.loads((config / ".claude.json").read_text())
        bundle = {
            "version": version,
            "fingerprint": fingerprint,
            "created_at": time.time(),
            "files": {name: json.loads((config / name).read_text()) for name in FILES},
            "features": {name: native_state[name] for name in FEATURE_FIELDS},
        }
        # Publish a complete initialization without replacing the previous bundle early.
        staged = home / "bundle.json"
        staged.write_text(json.dumps(bundle))
        staged.replace(bundle_path)


def restore(owner: Path, config: Path, version: str) -> bool:
    bundle_path = owner / ".cheese/native-startup-cache.json"
    if not bundle_path.exists():
        return False
    env, fingerprint = identity(owner)
    bundle = json.loads(bundle_path.read_text())
    if (
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") != env["CLAUDE_CODE_OAUTH_TOKEN"]
        or bundle["fingerprint"] != fingerprint
        or bundle["version"] != version
        or not 0 <= time.time() - bundle["created_at"] < MAX_AGE
    ):
        return False
    # Never copy account identity, project trust, or conversation state.
    features = {name: bundle["features"][name] for name in FEATURE_FIELDS}
    for name in FILES:
        destination = config / name
        # Native refresh owns existing files. Preserve all policy keys unchanged.
        if not destination.exists():
            destination.write_text(json.dumps(bundle["files"][name]))
    state_path = config / ".claude.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not any(name in state for name in FEATURE_FIELDS):
        state.update(features)
        state_path.write_text(json.dumps(state))
    return True


if __name__ == "__main__":
    os.umask(0o077)
    operation, owner_arg, version_arg = sys.argv[1:4]
    if operation == "prepare":
        prepare(Path(owner_arg), version_arg)
    elif operation == "restore":
        try:
            restored = restore(Path(owner_arg), Path(sys.argv[4]), version_arg)
            print(
                "native startup cache: reused"
                if restored
                else "native startup cache: miss"
            )
        except (OSError, ValueError, KeyError, TypeError):
            # A cache miss keeps native policy fetching enabled.
            print(
                "native startup cache: unreadable; native fetch will run",
                file=sys.stderr,
            )
    else:
        raise ValueError("Unknown cache operation")
