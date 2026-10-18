"""Split one read request's CPU between its phases, without cProfile's distortion.

cProfile answers "which function is called most", but its per-call hooks make a
pure-Python encoder look several times worse than it is, and it cannot say how
much of a 300 ms request was the ETag encode versus row assembly. This script
wraps the named phases with ``time.process_time`` deltas — the same metric the
load harness reads from ``/proc`` — and prints them against the request's own
CPU time.

    DATABASE_URL=... REDIS_URL=... JWT_SECRET=perf-secret \\
    PERF_PROJECT_ID=... PERF_ROOM_ID=... PERF_TASK_ID=... \\
        uv run --project backend python backend/scripts/perf_api_load/segments.py topics.list

Only one request is in flight, so a phase's process time is its own work (plus
whatever background sweep happened to land inside it; repeating the run shows
that noise).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

PROJECT_ID = os.environ["PERF_PROJECT_ID"]
ROOM_ID = os.environ["PERF_ROOM_ID"]
TASK_ID = os.environ["PERF_TASK_ID"]
HANDLE = os.environ.get("PERF_HANDLE", "perfviewer")
USER_ID = int(os.environ.get("PERF_USER_ID", "9"))

_spent: list[float] = []


def _record(index: int, started: float) -> None:
    # Every wrapper records its own inclusive CPU. Phases nest (the route calls
    # list_for_project, encoding runs last), so the column is "how much of the
    # request ran inside this phase", not a partition.
    _spent[index] += time.process_time() - started


def wrap_sync(module: Any, name: str, index: int) -> None:
    original = getattr(module, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.process_time()
        try:
            return original(*args, **kwargs)
        finally:
            _record(index, started)

    setattr(module, name, wrapper)


def wrap_async(module: Any, name: str, index: int) -> None:
    original = getattr(module, name)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.process_time()
        try:
            return await original(*args, **kwargs)
        finally:
            _record(index, started)

    setattr(module, name, wrapper)


#: (label, module path, attribute) per endpoint. Order defines the index.
PHASES = {
    "topics.list": [
        ("auth.resolve", "app.api.auth.resolver", "ActorResolver.resolve"),
        ("auth.authorize", "app.api.auth.resolver", "ActorResolver.authorize_project"),
        (
            "topics.list_for_project",
            "app.domain.topic.services",
            "TopicService.list_for_project",
        ),
        ("threads_of_rooms", "app.api.routes.topics", "threads_of_rooms"),
        ("awaiting_an_answer", "app.api.routes.topics", "_awaiting_an_answer"),
        ("moving_topics", "app.api.routes.topics", "moving_topics"),
        ("relevance", "app.domain.topic.services", "TopicService.relevance_for_topics"),
        ("live_cards", "app.api.topics_signals", "live_cards"),
        ("waits", "app.api.routes.topics", "MemberWaits.for_rooms"),
        (
            "tasks_of_rooms",
            "app.domain.room_task.services",
            "TaskService.tasks_of_rooms",
        ),
        ("rail_tasks", "app.api.routes.topics", "rail_tasks"),
        ("encode+etag", "app.api.conditional", "_encode_yielding"),
    ],
    "tasks.list": [
        ("auth.resolve", "app.api.auth.resolver", "ActorResolver.resolve"),
        ("auth.authorize", "app.api.auth.resolver", "ActorResolver.authorize_project"),
        (
            "task.list_in_project",
            "app.domain.room_task.services",
            "TaskService.list_in_project",
        ),
        ("incomplete_tasks", "app.api.routes.projects", "incomplete_tasks"),
        ("project_task_rows", "app.api.routes.projects", "project_task_rows"),
        ("encode+etag", "app.api.conditional", "_encode_yielding"),
    ],
    "history.page": [
        ("auth.resolve", "app.api.auth.resolver", "ActorResolver.resolve"),
        ("page_for_topic", "app.domain.block.paging", "PagingQueries.page_for_topic"),
        (
            "reactions",
            "app.domain.block.repositories",
            "BlockRepository.reactions_for_blocks",
        ),
    ],
    "accept-card": [
        ("auth.resolve", "app.api.auth.resolver", "ActorResolver.resolve"),
        (
            "list_for_topic",
            "app.domain.review.services.service",
            "AcceptService.list_for_topic",
        ),
        (
            "describe_many",
            "app.domain.review.services.service",
            "AcceptService.describe_many",
        ),
        (
            "refresh_snapshots",
            "app.domain.review.services.service",
            "AcceptService.refresh_stale_pr_snapshots",
        ),
    ],
}

PATHS = {
    "topics.list": f"/topics?project_id={PROJECT_ID}",
    "history.page": f"/topics/{ROOM_ID}/history?limit=50",
    "history.channel": f"/topics/{ROOM_ID}/history?limit=50&channel=true",
    "tasks.list": f"/projects/{PROJECT_ID}/tasks",
    "accept-card": f"/topics/{TASK_ID}/accept-card",
}


def install(label: str) -> list[str]:
    import importlib

    labels: list[str] = []
    for index, (name, module_path, attribute) in enumerate(PHASES[label]):
        module = importlib.import_module(module_path)
        _spent.append(0.0)
        labels.append(name)
        if "." in attribute:
            owner_name, method = attribute.split(".")
            owner = getattr(module, owner_name)
            setattr(owner, method, _wrap_callable(getattr(owner, method), index))
        else:
            target = getattr(module, attribute)
            if asyncio.iscoroutinefunction(target):
                wrap_async(module, attribute, index)
            else:
                wrap_sync(module, attribute, index)
    return labels


def _wrap_callable(original: Callable, index: int) -> Callable:
    if asyncio.iscoroutinefunction(original):

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.process_time()
            try:
                return await original(*args, **kwargs)
            finally:
                _record(index, started)
    else:

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.process_time()
            try:
                return original(*args, **kwargs)
            finally:
                _record(index, started)

    return wrapper


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "topics.list"
    labels = install(which)

    from fastapi.testclient import TestClient

    from app.common.auth import create_access_token
    from app.main import app

    headers = {"Authorization": f"Bearer {create_access_token(USER_ID, handle=HANDLE)}"}
    path = PATHS[which]
    with TestClient(app) as client:
        client.get(path, headers=headers)  # warm connections and statements
        for label in labels:
            _spent[labels.index(label)] = 0.0
        started = time.process_time()
        response = client.get(path, headers=headers)
        total = time.process_time() - started
        assert response.status_code == 200, response.text[:300]
        print(
            f"endpoint={which} body={len(response.content)} bytes total_cpu={total * 1000:.1f}ms"
        )
        for index, label in enumerate(labels):
            print(f"  {label:26s} {_spent[index] * 1000:8.1f} ms")


if __name__ == "__main__":
    main()
