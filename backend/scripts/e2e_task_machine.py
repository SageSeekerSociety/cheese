"""Serve the browser file-editing fixture through an enrolled device connection."""

import asyncio
import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import boto3
import httpx
from websockets.asyncio.client import connect

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness import deployment_harness
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.agent_session.services import AgentSessionService
from app.domain.review.pr_publish import _draft_pr_for_one_task
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService


async def poll_approved_device(
    client: httpx.AsyncClient, code: str, interval: float, timeout: float = 30
) -> dict:
    # The poll can still report pending after the approval request succeeds.
    async with asyncio.timeout(timeout):
        while True:
            response = await client.post(
                "/connector/auth/device/poll", json={"device_code": code}
            )
            response.raise_for_status()
            device = response.json()
            if device["status"] == "approved":
                return device
            if device["status"] != "pending":
                raise RuntimeError(
                    f"device authorization ended with {device['status']!r}"
                )
            await asyncio.sleep(interval)


async def main():
    request = json.load(sys.stdin)
    base = request["api"]
    project, room_id = request["project"], uuid.UUID(request["room"])
    home = Path(request["home"]).resolve()
    home.mkdir(parents=True)
    state = home / "executor"
    state.mkdir()
    async with async_session_factory() as session:
        room = await session.get(Topic, room_id)
        assert room is not None and str(room.project_id) == project
        resource = str(room.resource_id or room.id)
        actor = await TopicMemberService(session).resolve_agent_handle(room_id)
    env = dict(
        os.environ,
        HOME=str(home),
        PATH=str(Path(__file__).resolve().parents[1] / "sandbox")
        + os.pathsep
        + os.environ["PATH"],
        CHEESE_API=base,
        CHEESE_PROJECT=project,
        CHEESE_TOPIC=str(room_id),
        CHEESE_TOKEN=mint_scoped_token(
            project_id=project, topic_id=str(room_id), agent_handle=actor
        ),
        GIT_AUTHOR_NAME=actor,
        GIT_AUTHOR_EMAIL=f"{actor}@agent.cheese.local",
        GIT_COMMITTER_NAME=actor,
        GIT_COMMITTER_EMAIL=f"{actor}@agent.cheese.local",
    )

    def seed():
        storage = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        if settings.transcript_s3_bucket not in {
            bucket["Name"] for bucket in storage.list_buckets()["Buckets"]
        }:
            storage.create_bucket(Bucket=settings.transcript_s3_bucket)
        for task in request["tasks"]:
            subprocess.run(
                ["cheese", "worktree", task["id"]],
                env=env,
                check=True,
                stdout=sys.stderr,
            )
            tree = home / ".cheese/tasks" / task["id"]
            file = tree / "src/login.txt"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(task["content"])
            for args in (["add", "src/login.txt"], ["commit", "-m", "Seed task file"]):
                subprocess.run(
                    ["git", "-C", str(tree), *args],
                    env=env,
                    check=True,
                    stdout=sys.stderr,
                )
            subprocess.run(
                ["cheese", "sync", "--task", task["id"]],
                cwd=tree,
                env=env,
                check=True,
                stdout=sys.stderr,
            )

    await asyncio.to_thread(seed)
    # Run the scheduled publication step now, before opening the browser panel.
    for task in request["tasks"]:
        async with async_session_factory() as session:
            assert await _draft_pr_for_one_task(session, uuid.UUID(task["id"]))
            await session.commit()
    (state / "config.json").write_text(
        json.dumps({"workspace": str(home), "env": {"HOME": str(home)}})
    )
    executor = Executor(state)
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        response = await client.post(
            "/connector/auth/device/start", json={"device_name": "E2E task files"}
        )
        response.raise_for_status()
        start = response.json()
        code = start["device_code"]
        response = await client.post(
            "/connector/connect",
            headers={"Authorization": "Bearer " + request["token"]},
            json={"device_code": code, "project_id": project},
        )
        response.raise_for_status()
        device = await poll_approved_device(client, code, start["interval"])
    url = base.replace("http", "ws", 1) + "/connector/agent?token=" + device["token"]
    async with connect(url) as socket:
        assert json.loads(await socket.recv())["t"] == "welcome"
        await socket.send(json.dumps({"t": "hello", "executor": True}))
        async with async_session_factory() as session:
            await AgentSessionService(session).remember_place(
                topic_id=room_id,
                agent_handle=actor,
                work_lease={
                    "kind": "device",
                    "device_id": device["device_id"],
                    "home": str(home),
                    "state": str(state),
                },
                runtime_location={
                    "device_id": device["device_id"],
                    "channel": "central",
                    "resource_id": resource,
                },
                harness=deployment_harness(),
            )
            await session.commit()
        print("ready", flush=True)
        async for raw in socket:
            frame = json.loads(raw)
            if frame["t"] != "execution.call":
                continue
            call = json.loads(frame["stdin"])
            try:
                assert call["method"] == "task_fs", call["method"]
                result = await asyncio.to_thread(executor.task_fs, call["params"])
                payload = json.dumps({"result": result}).encode()
                await socket.send(
                    json.dumps(
                        {
                            "t": "execution.data",
                            "id": frame["id"],
                            "data": base64.b64encode(payload).decode(),
                        }
                    )
                )
                error = ""
            except Exception as exc:
                error = str(exc)
            await socket.send(
                json.dumps({"t": "execution.result", "id": frame["id"], "error": error})
            )


if __name__ == "__main__":
    asyncio.run(main())
