#!/usr/bin/env python3
"""Provision the deployment's Forgejo admin and persistent event relay settings."""

import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlsplit
import uuid


def environment_values(content: str) -> dict[str, str]:
    values = {}
    for line in content.splitlines():
        key, separator, value = line.strip().removeprefix("export ").partition("=")
        if not separator or not key.startswith(("FORGE", "FRONTEND_URL")):
            continue
        # Compose accepts unquoted JSON; shell tokenization would remove its quotes.
        if value.startswith("{"):
            json.loads(value)
            values[key] = value
        else:
            parts = shlex.split(value, comments=True)
            if len(parts) > 1:
                raise ValueError(f"Invalid {key} in backend environment")
            values[key] = parts[0] if parts else ""
    return values


def save_environment(path: Path, updates: dict[str, str]) -> None:
    original = path.read_text() if path.exists() else ""
    lines = [
        line
        for line in original.splitlines()
        if line.strip().removeprefix("export ").partition("=")[0] not in updates
    ]
    content = (
        "\n".join(lines + [f"{key}={value}" for key, value in updates.items()]) + "\n"
    )
    if content == original:
        return
    if path.exists():
        atomic_private(
            path.with_name(path.name + "." + uuid.uuid4().hex + ".bak"), original
        )
    atomic_private(path, content)


def configure_events(backend_env: Path, relay_env: Path) -> None:
    values = environment_values(backend_env.read_text())
    keys = ("FORGE_EVENT_RELAY_URL", "FORGE_WEBHOOK_URL", "FORGE_EVENT_SECRET")
    configured = [bool(values.get(key)) for key in keys]
    if any(configured) and not all(configured):
        raise ValueError(
            "Event relay requires its connection URL, webhook URL and secret together"
        )
    relay_values = (
        environment_values(relay_env.read_text()) if relay_env.exists() else {}
    )
    routes = json.loads(relay_values.get("FORGE_EVENT_RELAY_KEYS", "{}"))
    if all(configured):
        webhook = urlsplit(values["FORGE_WEBHOOK_URL"])
        connection = urlsplit(values["FORGE_EVENT_RELAY_URL"])
        if (
            webhook.scheme not in {"http", "https"}
            or connection.scheme != {"http": "ws", "https": "wss"}.get(webhook.scheme)
            or connection.netloc != webhook.netloc
            or connection.path != webhook.path.rstrip("/") + "/connect"
            or not webhook.hostname
            or webhook.username
            or webhook.password
            or webhook.query
            or webhook.fragment
            or connection.query
            or connection.fragment
        ):
            raise ValueError(
                "Event relay URLs must identify one HTTP(S) webhook and its WS(S) connection"
            )
        deployment = webhook.path.rstrip("/").rsplit("/", 1)[-1]
        if (
            deployment
            in {
                values.get("FORGE_EVENT_LOCAL_DEPLOYMENT"),
                relay_values.get("FORGE_EVENT_LOCAL_DEPLOYMENT"),
            }
            and routes.get(deployment) != values["FORGE_EVENT_SECRET"]
        ):
            raise ValueError(
                "The local event relay credential differs from the backend credential"
            )
        local = routes.get(deployment) == values["FORGE_EVENT_SECRET"]
    else:
        public = urlsplit(values.get("FRONTEND_URL", ""))
        if (
            public.scheme not in {"http", "https"}
            or not public.hostname
            or public.username
            or public.password
            or public.query
            or public.fragment
        ):
            raise ValueError(
                "Set FRONTEND_URL before configuring the local event relay"
            )
        # Save the relay first. A retry after an interrupted backend write reuses it.
        deployment = (
            relay_values.get("FORGE_EVENT_LOCAL_DEPLOYMENT") or uuid.uuid4().hex
        )
        secret = routes.get(deployment) or secrets.token_hex(32)
        routes[deployment] = secret
        save_environment(
            relay_env,
            {
                "FORGE_EVENT_LOCAL_DEPLOYMENT": deployment,
                "FORGE_EVENT_RELAY_KEYS": json.dumps(routes, separators=(",", ":")),
            },
        )
        base = values["FRONTEND_URL"].rstrip("/") + "/api/forge/events/" + deployment
        save_environment(
            backend_env,
            {
                "FORGE_EVENT_RELAY_URL": base.replace("http", "ws", 1) + "/connect",
                "FORGE_WEBHOOK_URL": base,
                "FORGE_EVENT_SECRET": secret,
                "FORGE_EVENT_LOCAL_DEPLOYMENT": deployment,
            },
        )
        local = True
    # These values are consumed by the deployment shell; credentials stay in files.
    print("export FORGE_EVENTS_LOCAL=" + ("true" if local else "false"))
    print("export FORGE_EVENTS_ENV_FILE=" + shlex.quote(str(relay_env.resolve())))
    print(
        "export FORGE_EVENTS_UPSTREAM="
        + ("forge-events:8093" if local else "backend:8081")
    )


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
    parser.add_argument("--configure-events", action="store_true")
    parser.add_argument("--relay-env", type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:3300/api/v1")
    parser.add_argument("--docker-context")
    args = parser.parse_args()
    # Refuse a wrong path before creating an administrator or issuing a token.
    original = args.backend_env.read_text()
    if args.configure_events:
        configure_events(
            args.backend_env,
            args.relay_env or args.backend_env.parent / ".forge-events.env",
        )
        return
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
