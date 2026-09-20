#!/usr/bin/env python3
"""Provision the deployment admin and save its token in the backend env file."""

import argparse
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlsplit
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
    parser.add_argument("--container")
    parser.add_argument("--backend-env", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:3300/api/v1")
    parser.add_argument("--docker-context")
    args = parser.parse_args()
    # Refuse a wrong path before creating an administrator or issuing a token.
    original = args.backend_env.read_text()
    if args.prepare:
        values = {}
        for line in original.splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and key in {
                "FRONTEND_URL",
                "FORGEJO_URL",
                "FORGE_WEBHOOK_URL",
            }:
                parts = shlex.split(value, comments=True)
                if len(parts) > 1:
                    raise ValueError(f"Invalid {key} in backend environment")
                values[key] = parts[0] if parts else ""
        public = os.environ.get("FORGEJO_URL") or values.get("FORGEJO_URL")
        if not public:
            frontend = values.get("FRONTEND_URL", "")
            if not frontend:
                raise ValueError("Set FRONTEND_URL or FORGEJO_URL before deploying")
            public = frontend.rstrip("/") + "/forge/"
        target = urlsplit(public)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username
            or target.password
            or target.query
            or target.fragment
        ):
            raise ValueError("FORGEJO_URL must be an HTTP(S) URL without credentials")
        webhook = (
            os.environ.get("FORGE_WEBHOOK_URL")
            or values.get("FORGE_WEBHOOK_URL")
            or public
        )
        hosts = os.environ.get("FORGEJO_WEBHOOK_HOSTS") or urlsplit(webhook).hostname
        if not hosts:
            raise ValueError("FORGE_WEBHOOK_URL must contain a hostname")
        print("export FORGEJO_URL=" + shlex.quote(public.rstrip("/") + "/"))
        print("export FORGEJO_WEBHOOK_HOSTS=" + shlex.quote(hosts))
        return
    if not args.container:
        parser.error("--container is required when provisioning the administrator")
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
