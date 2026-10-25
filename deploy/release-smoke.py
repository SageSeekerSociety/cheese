"""Use a release the way a person does, on its new slots, before it takes traffic.

`/readyz` says a backend process can serve; it does not say that anyone can sign
in, open a task or hear from the server. So on a box with app-router slots,
deploy-docker.sh (`smoke_release`) runs this file twice per release, after the
new backend and frontend slots pass their health checks and before app-router
is switched to them. Any failure leaves app-router where it was: the new slots
are removed and the release stops, so a broken release never takes traffic.

  python - provision
      In this release's backend image with the box's backend env (`compose
      run`). Makes sure the smoke account exists and signs in with the password
      in DEPLOY_SMOKE_PASSWORD (the box's ~/ops/deploy-smoke.secret). The
      account is the deploy's own, not a person's: handle `deploy-smoke`, an
      address the platform never mails.

  python - check --backend URL [--frontend URL]
      In the same image on the host network, against the new slots' loopback
      ports, through the same API the web app calls:
        frontend-index  GET / answers 200 and names a hashed script
        frontend-asset  that script answers 200 and is not empty
        login           POST /users/auth/login with the password
        me              GET /users/me names the smoke account
        task            the account's own task, made once (a project with a
                        fixed id, one task in its 综合 room), opened with
                        GET /topics/{task}/task, as the task page does
        websocket       WS /notifications/inbox, the connection every signed-in
                        page holds, sends its first frame
        logout          POST /users/auth/logout ends the session the check opened

Every check is bounded (TIMEOUT seconds) and prints one line, `smoke <check> ok
<what it got>` or `smoke <check> FAILED <what it got>`; the first failure ends the
run with status 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid

HANDLE = "deploy-smoke"
# A domain the platform never mails (app/core/email.py PLACEHOLDER_EMAIL_DOMAINS).
EMAIL = "deploy-smoke@placeholder.internal"
NICKNAME = "部署冒烟"
# Fixed, so creating it is idempotent (POST /projects returns the earlier project
# for an id its owner already holds).
PROJECT_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://okcheese.com/deploy-smoke/project")
PROJECT_NAME = "部署冒烟"
TASK_TITLE = "部署冒烟：打开这件任务"
TIMEOUT = 10.0


class Failed(Exception):
    def __init__(self, check: str, detail: str):
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


def passed(check: str, detail: str) -> None:
    print(f"smoke {check} ok {detail}", flush=True)


async def provision(password: str) -> None:
    # The backend's own modules: this mode runs in the release's image.
    from app.db.session import AsyncSessionLocal
    from app.domain.dashboard.user_statistics import UserStatisticsRepository
    from app.domain.user.passwords import hash_password
    from app.domain.user.repositories import UserProfileRepository, UserRepository
    from app.domain.user.services import UserAuthService

    async with AsyncSessionLocal() as db:
        users = UserRepository(session=db)
        auth = UserAuthService(
            user_repo=users,
            profile_repo=UserProfileRepository(session=db),
            stats_repo=UserStatisticsRepository(session=db),
        )
        user = await users.get_by_username(HANDLE)
        if user is None:
            await auth.register_with_password(
                username=HANDLE, nickname=NICKNAME, email=EMAIL, password=password
            )
            state = "created"
        elif not await auth.verify_password(user, password):
            # The secret file was replaced: the account follows it.
            await users.update_password(user.id, await hash_password(password))
            state = "password set from the secret file"
        else:
            state = "present"
        await db.commit()
    passed("provision", f"account {HANDLE} {state}")


def request(client, check: str, method: str, url: str, **kwargs):
    """One bounded request; a transport error fails the check it belongs to."""
    import httpx

    try:
        return client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise Failed(check, f"{method} {url}: {type(exc).__name__}: {exc}") from None


def data(client, check: str, method: str, url: str, **kwargs):
    """The `data` of an API answer that must be 200."""
    response = request(client, check, method, url, **kwargs)
    if response.status_code != 200:
        raise Failed(
            check, f"{method} {url}: HTTP {response.status_code} {response.text[:300]}"
        )
    try:
        return response.json()["data"]
    except (ValueError, KeyError, TypeError):
        raise Failed(
            check, f"{method} {url}: not the API envelope: {response.text[:300]}"
        ) from None


def check_frontend(client, frontend: str) -> None:
    response = request(client, "frontend-index", "GET", f"{frontend}/")
    if response.status_code != 200:
        raise Failed("frontend-index", f"GET {frontend}/: HTTP {response.status_code}")
    found = re.search(r'src="(/assets/[^"]+\.js)"', response.text)
    if found is None:
        raise Failed(
            "frontend-index", f"no /assets/ script in {len(response.text)} bytes"
        )
    asset = found.group(1)
    passed("frontend-index", f"HTTP 200, {len(response.text)} bytes, names {asset}")
    response = request(client, "frontend-asset", "GET", f"{frontend}{asset}")
    if response.status_code != 200 or not response.content:
        raise Failed(
            "frontend-asset",
            f"{asset}: HTTP {response.status_code}, {len(response.content)} bytes",
        )
    passed("frontend-asset", f"{asset}: HTTP 200, {len(response.content)} bytes")


def own_task(client, backend: str, auth: dict) -> str:
    """The smoke account's task, made the first time; its id."""
    projects = data(client, "task", "GET", f"{backend}/projects", headers=auth)
    project = next(
        (p for p in projects["data"] if p.get("id") == str(PROJECT_ID)), None
    )
    if project is None:
        project = data(
            client,
            "task",
            "POST",
            f"{backend}/projects",
            headers=auth,
            json={"id": str(PROJECT_ID), "name": PROJECT_NAME},
        )
    room = project.get("root_topic_id")
    if not room:
        raise Failed(
            "task", f"project {PROJECT_ID} has no room: {json.dumps(project)[:300]}"
        )
    tasks = data(
        client, "task", "GET", f"{backend}/topics/{room}/tasks?limit=0", headers=auth
    )
    task = next((t for t in tasks["data"] if t.get("title") == TASK_TITLE), None)
    if task is None:
        task = data(
            client,
            "task",
            "POST",
            f"{backend}/topics/{room}/tasks",
            headers=auth,
            json={"title": TASK_TITLE},
        )
    return task["id"]


def check_backend(client, backend: str, password: str) -> None:
    from websockets.sync.client import connect

    url = f"{backend}/users/auth/login"
    response = request(
        client, "login", "POST", url, json={"username": HANDLE, "password": password}
    )
    if response.status_code != 200:
        raise Failed(
            "login", f"POST {url}: HTTP {response.status_code} {response.text[:300]}"
        )
    token = (response.json().get("data") or {}).get("accessToken")
    if not token:
        raise Failed("login", f"no access token: {response.text[:300]}")
    refresh = response.cookies.get("cheese_refresh")
    auth = {"Authorization": f"Bearer {token}"}
    passed("login", f"HTTP 200, signed in as {HANDLE}")

    me = data(client, "me", "GET", f"{backend}/users/me", headers=auth)
    name = (me.get("user") or {}).get("username")
    if name != HANDLE:
        raise Failed("me", f"names {name!r}, not {HANDLE}")
    passed("me", f"HTTP 200, user {name}")

    task_id = own_task(client, backend, auth)
    task = data(client, "task", "GET", f"{backend}/topics/{task_id}/task", headers=auth)
    if task.get("id") != task_id:
        raise Failed(
            "task", f"GET /topics/{task_id}/task answered task {task.get('id')!r}"
        )
    passed("task", f"HTTP 200, task {task_id} {task.get('title')!r}")

    ticket = data(
        client, "websocket", "POST", f"{backend}/notifications/live-token", headers=auth
    )
    url = re.sub(r"^http", "ws", backend) + "/notifications/inbox"
    try:
        with connect(url, open_timeout=TIMEOUT, close_timeout=2) as socket:
            socket.send(json.dumps({"token": ticket["token"]}))
            frame = json.loads(socket.recv(timeout=TIMEOUT))
    except Exception as exc:  # noqa: BLE001 - whatever it was is what the check got
        raise Failed("websocket", f"{url}: {type(exc).__name__}: {exc}") from None
    if not isinstance(frame, dict) or frame.get("kind") != "inbox":
        raise Failed("websocket", f"first frame {json.dumps(frame)[:300]}")
    passed("websocket", f"first frame {json.dumps(frame)}")

    # The refresh cookie is Secure and scoped to the gateway's path, so it is
    # handed back by name rather than through the cookie jar.
    data(
        client,
        "logout",
        "POST",
        f"{backend}/users/auth/logout",
        headers={"Cookie": f"cheese_refresh={refresh}"} if refresh else {},
    )
    passed("logout", "HTTP 200, session ended")


def check(backend: str, frontend: str | None, password: str) -> None:
    import httpx

    with httpx.Client(timeout=TIMEOUT) as client:
        if frontend:
            check_frontend(client, frontend)
        else:
            print(
                "smoke frontend skipped: this box does not roll the frontend",
                flush=True,
            )
        check_backend(client, backend, password)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["provision", "check"])
    parser.add_argument("--backend")
    parser.add_argument("--frontend")
    args = parser.parse_args()
    password = os.environ.get("DEPLOY_SMOKE_PASSWORD", "")
    if not password:
        print("smoke FAILED: DEPLOY_SMOKE_PASSWORD is empty", flush=True)
        return 1
    try:
        if args.mode == "provision":
            asyncio.run(provision(password))
        else:
            check(
                args.backend.rstrip("/"),
                (args.frontend or "").rstrip("/") or None,
                password,
            )
    except Failed as failure:
        print(f"smoke {failure.check} FAILED {failure.detail}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
