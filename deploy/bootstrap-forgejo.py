#!/usr/bin/env python3
"""Provision the deployment admin and save its token in the backend env file."""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import uuid


def atomic_private(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--backend-env", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:3300/api/v1")
    parser.add_argument("--docker-context")
    args = parser.parse_args()
    # Refuse a wrong path before creating an administrator or issuing a token.
    original = args.backend_env.read_text()
    logging.basicConfig(
        filename=args.backend_env.parent / "forgejo-bootstrap.log",
        level=logging.INFO,
        format="%(asctime)s %(message)s",
    )
    log = logging.getLogger("forgejo-bootstrap")
    log.info("start container=%s backend_env=%s", args.container, args.backend_env)
    docker = ["docker"]
    if args.docker_context:
        docker += ["--context", args.docker_context]
    command = docker + ["exec", args.container, "forgejo", "admin", "user"]

    def run(*arguments: str) -> str:
        result = subprocess.run(
            command + list(arguments), capture_output=True, text=True, check=False
        )
        if result.returncode:
            # Admin command output can contain generated passwords or tokens.
            raise RuntimeError(
                f"Forgejo admin {arguments[0]} failed ({result.returncode})"
            )
        return result.stdout.strip()

    username = "cheese-platform"
    token_path = args.backend_env.parent / ".forgejo-admin-token"
    if token_path.exists():
        token = token_path.read_text().strip()
        log.info("admin_token status=reused path=%s", token_path)
    else:
        users = run("list")
        if not any(
            len(parts := line.split()) > 1 and parts[1] == username
            for line in users.splitlines()
        ):
            run(
                "create",
                "--username",
                username,
                "--email",
                "cheese-platform@users.invalid",
                "--admin",
                "--random-password",
                "--random-password-length",
                "48",
                "--must-change-password=false",
            )
            log.info("admin_account status=created")
        else:
            log.info("admin_account status=reused")
        token = run(
            "generate-access-token",
            "--username",
            username,
            "--token-name",
            "cheese-platform-" + uuid.uuid4().hex,
            "--scopes",
            "all",
            "--raw",
        )
        if len(token) != 40 or any(c not in "0123456789abcdef" for c in token):
            raise RuntimeError("Forgejo returned an invalid token format")
        atomic_private(token_path, token + "\n")
        log.info("admin_token status=saved path=%s", token_path)
    request = urllib.request.Request(
        args.api_url.rstrip("/") + "/user",
        headers={"Authorization": "token " + token},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        user = json.load(response)
    if user.get("login") != username or not user.get("is_admin"):
        raise RuntimeError("Saved token does not identify the deployment admin")
    lines = original.splitlines()
    replacement = "FORGEJO_ADMIN_TOKEN=" + token
    updated = [
        line
        for line in lines
        if not line.strip().removeprefix("export ").startswith("FORGEJO_ADMIN_TOKEN=")
    ] + [replacement]
    content = "\n".join(updated) + "\n"
    if content != original:
        backup = args.backend_env.with_name(
            args.backend_env.name + "." + uuid.uuid4().hex + ".bak"
        )
        atomic_private(backup, original)
        atomic_private(args.backend_env, content)
        log.info("backend_env status=updated backup=%s", backup)
    else:
        log.info("backend_env status=unchanged")
    log.info("complete admin_verified=true")
    print("Forgejo admin verified; backend credentials saved.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.getLogger("forgejo-bootstrap").error(
            "failed exception=%s", type(exc).__name__
        )
        raise
