"""Use a release the way a person does, on its new slots, before it takes traffic.

`/readyz` says a backend process can serve; it does not say that anyone can sign
in, open a task or hear from the server. So on a box with app-router slots,
deploy-docker.sh (`smoke_release`) runs this file twice per release, after the
new backend and frontend slots pass their health checks and before app-router
is switched to them. Any failure leaves app-router where it was: the new slots
are removed and the release stops, so a broken release never takes traffic.

The account is the deploy's own: it is the one account at SMOKE_EMAIL, an
address the platform never mails, so nobody can register it or receive a code
for it, and finding the account by it can never pick a person's. Its handle is
random, so nobody can take it first. It has no password. Sign-in failures are
counted per handle (app/domain/user/login_security.py) and handles are readable
by any signed-in user (`GET /users/{id}`), so a release that signed in with a
password could be held back by anyone sending wrong ones; this one opens its
session inside the release's image instead.

  python - provision
      In this release's backend image with the box's backend env (`compose
      run`). Finds or makes the account and opens a session for it. Prints
      `smoke-session <user id> <access token> <refresh token>`, which the
      deploy hands to `check` through the environment and never logs.

  python - check --backend URL [--frontend URL]
      In the same image on the host network, against the new slots' loopback
      ports, through the same API the web app calls, as that session
      (DEPLOY_SMOKE_USER_ID, DEPLOY_SMOKE_TOKEN, DEPLOY_SMOKE_REFRESH):
        frontend-index  GET / answers 200 and names a hashed script
        frontend-asset  that script answers 200 and is not empty
        login           POST /users/auth/login refuses a wrong password for an
                        account that does not exist with 401 invalid_credentials:
                        the route, its rate limiter and the account lookup work,
                        and no account's failure count moves
        me              GET /users/me names the smoke account
        task            the account's own task, made once (a project it owns
                        by name, one task in its 综合 room), opened with
                        GET /topics/{task}/task, as the task page does
        websocket       WS /notifications/inbox, the connection every signed-in
                        page holds, sends its first frame
        logout          POST /users/auth/logout with the session's refresh token

Every step is bounded (TIMEOUT seconds a request, CREATE_TIMEOUT for the
first run's project, which provisions a repository, PROVISION_TIMEOUT for the
provision) and prints one line, `smoke <check> ok <what it got>` or `smoke
<check> FAILED <what it got>`; the first failure ends the run with status 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys

# A domain the platform never mails (app/core/email.py PLACEHOLDER_EMAIL_DOMAINS).
SMOKE_EMAIL = "deploy-smoke@placeholder.internal"
NICKNAME = "部署冒烟"
INTRO = (
    "部署冒烟测试账号：每次发布切流量前，部署用它把新版本走一遍。不是真人，没有密码。"
)
PROJECT_NAME = "部署冒烟"
TASK_TITLE = "部署冒烟：打开这件任务"
TIMEOUT = 10.0
CREATE_TIMEOUT = 60.0
PROVISION_TIMEOUT = 60.0


class Failed(Exception):
    def __init__(self, check: str, detail: str):
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


def passed(check: str, detail: str) -> None:
    print(f"smoke {check} ok {detail}", flush=True)


async def provision() -> tuple[int, str, str]:
    """The smoke account's id, and an access and a refresh token of a new session."""
    # The backend's own modules: this mode runs in the release's image.
    from app.common.auth import create_access_token
    from app.db.session import AsyncSessionLocal
    from app.domain.user.repositories import UserProfileRepository, UserRepository
    from app.domain.user.sessions import SessionService

    async with AsyncSessionLocal() as db:
        users = UserRepository(session=db)
        user = await users.get_by_email(SMOKE_EMAIL)
        state = "present"
        if user is None:
            user = await users.create_user(
                username=secrets.token_hex(12), email=SMOKE_EMAIL, hashed_password=None
            )
            await UserProfileRepository(session=db).create_profile(
                user_id=user.id, nickname=NICKNAME, intro=INTRO, avatar_id=1
            )
            state = "created"
        started = await SessionService(db).start(
            user.id, "deploy-smoke", ip="", user_agent="deploy smoke test"
        )
        token = create_access_token(
            user.id, handle=user.username, sid=started.session_id
        )
        await db.commit()
    passed("provision", f"account {user.id} {state}, session opened")
    return user.id, token, started.refresh_token


async def bounded_provision() -> tuple[int, str, str]:
    try:
        return await asyncio.wait_for(provision(), PROVISION_TIMEOUT)
    except TimeoutError:
        raise Failed(
            "provision", f"no answer from the database within {PROVISION_TIMEOUT:.0f}s"
        ) from None


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


def own_task(client, backend: str, auth: dict, handle: str) -> str:
    """The smoke account's task, made the first time; its id.

    Its project is the one it owns by PROJECT_NAME. The server picks a new
    project's id: an id the deploy could work out, anybody could too, and
    taking it first would hold every release back."""
    projects = data(client, "task", "GET", f"{backend}/projects", headers=auth)
    project = next(
        (
            p
            for p in projects["data"]
            if p.get("owner_handle") == handle and p.get("name") == PROJECT_NAME
        ),
        None,
    )
    if project is None:
        project = data(
            client,
            "task",
            "POST",
            f"{backend}/projects",
            headers=auth,
            json={"name": PROJECT_NAME},
            timeout=CREATE_TIMEOUT,
        )
    room = project.get("root_topic_id")
    if not room:
        raise Failed(
            "task",
            f"project {project.get('id')} has no room: {json.dumps(project)[:300]}",
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


def check_backend(client, backend: str, user_id: int, token: str, refresh: str) -> None:
    from websockets.sync.client import connect

    url = f"{backend}/users/auth/login"
    response = request(
        client,
        "login",
        "POST",
        url,
        json={"username": secrets.token_hex(12), "password": secrets.token_hex(16)},
    )
    if response.status_code != 401 or "invalid_credentials" not in response.text:
        raise Failed(
            "login",
            f"POST {url} with a wrong password: HTTP {response.status_code} "
            f"{response.text[:300]}, not 401 invalid_credentials",
        )
    passed("login", "HTTP 401 invalid_credentials for a wrong password, as expected")

    auth = {"Authorization": f"Bearer {token}"}
    me = data(client, "me", "GET", f"{backend}/users/me", headers=auth)
    named = (me.get("user") or {}).get("id")
    if named != user_id:
        raise Failed("me", f"names user {named!r}, not {user_id}")
    passed("me", f"HTTP 200, user {named}")

    task_id = own_task(client, backend, auth, me["user"]["username"])
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
    # handed over by name rather than through a cookie jar.
    data(
        client,
        "logout",
        "POST",
        f"{backend}/users/auth/logout",
        headers={"Cookie": f"cheese_refresh={refresh}"},
    )
    passed("logout", "HTTP 200")


def check(backend: str, frontend: str | None) -> None:
    import httpx

    try:
        user_id = int(os.environ["DEPLOY_SMOKE_USER_ID"])
        token = os.environ["DEPLOY_SMOKE_TOKEN"]
        refresh = os.environ["DEPLOY_SMOKE_REFRESH"]
    except (KeyError, ValueError):
        raise Failed("setup", "no session from provision in the environment") from None
    with httpx.Client(timeout=TIMEOUT) as client:
        if frontend:
            check_frontend(client, frontend)
        else:
            print(
                "smoke frontend skipped: this box does not roll the frontend",
                flush=True,
            )
        check_backend(client, backend, user_id, token, refresh)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["provision", "check"])
    parser.add_argument("--backend")
    parser.add_argument("--frontend")
    args = parser.parse_args()
    try:
        if args.mode == "provision":
            user_id, token, refresh = asyncio.run(bounded_provision())
            print(f"smoke-session {user_id} {token} {refresh}", flush=True)
        else:
            check(args.backend.rstrip("/"), (args.frontend or "").rstrip("/") or None)
    except Failed as failure:
        print(f"smoke {failure.check} FAILED {failure.detail}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
