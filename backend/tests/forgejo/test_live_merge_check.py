"""Opt-in: a PR's conflict decided by git against the pinned Forgejo.

Forgejo answers `mergeable: false` on a conflicting PR and on one whose merge
check is still running alike; the platform asks git (`merge_check`) and only a
test merge that conflicts is a conflict. This runs that path against the real
forge: its git transport, its credential, and a fetch by commit id.
"""

import asyncio
import base64
import os
import uuid
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.domain.review import merge_check
from app.domain.review.forgejo_pr import ForgejoClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGEJO_TEST_TOKEN_FILE"),
    reason="isolated Forgejo not configured",
)

API = "http://127.0.0.1:33086/api/v1"


async def _write(client, repo, path, text, **where):
    response = await client.post(
        f"/repos/{repo}/contents/{path}",
        json={"content": base64.b64encode(text.encode()).decode(), "message": path}
        | where,
    )
    assert response.status_code == 201, response.text


async def _edit(client, repo, path, text, branch):
    current = await client.get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
    current.raise_for_status()
    response = await client.put(
        f"/repos/{repo}/contents/{path}",
        json={
            "content": base64.b64encode(text.encode()).decode(),
            "message": f"edit {path}",
            "sha": current.json()["sha"],
            "branch": branch,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_a_conflicting_pr_is_a_conflict_once_git_has_merged_it(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    token = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    async with httpx.AsyncClient(
        base_url=API, headers={"Authorization": f"token {token}"}
    ) as client:
        name = f"merge-check-{uuid.uuid4().hex[:8]}"
        made = await client.post(
            "/user/repos",
            json={"name": name, "auto_init": True, "default_branch": "main"},
        )
        made.raise_for_status()
        owner = made.json()["owner"]["login"]
        repo = f"{owner}/{name}"
        await _write(client, repo, "notes.md", "start\n", branch="main")
        await _write(client, repo, "task.md", "task\n", new_branch="task")
        await _edit(client, repo, "notes.md", "the task's line\n", "task")
        await _edit(client, repo, "notes.md", "main's line\n", "main")
        opened = await client.post(
            f"/repos/{repo}/pulls", json={"head": "task", "base": "main", "title": "t"}
        )
        opened.raise_for_status()
        number = opened.json()["number"]

    forgejo = ForgejoClient(API)
    args = dict(owner=owner, repo=name, number=number, token=token)
    first = await forgejo.pull_request_status(**args)
    assert first.mergeable is not False
    async with asyncio.timeout(60):
        while (
            merge_check.answer(
                forgejo.git_url(owner, name), first.base_sha, first.head_sha
            )
            is None
        ):
            await asyncio.sleep(0.1)
            first = await forgejo.pull_request_status(**args)
    later = await forgejo.pull_request_status(**args)
    assert (later.mergeable, later.mergeable_state) == (False, "dirty")
