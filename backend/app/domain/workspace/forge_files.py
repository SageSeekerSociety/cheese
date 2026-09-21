"""Read committed files from the forge and unfinished files from their executor."""

import asyncio
import base64
import uuid
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    GatewayUnavailableError,
    NotFoundError,
    ValidationError,
)
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceNotReady, DeviceOffline
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.forge import (
    binding_for_project,
    branch_head,
    default_branch,
    repository_data,
    status_client,
    tokens_for_project,
)
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic
from app.domain.workspace.textfile import (
    MAX_TEXT_BYTES,
    compare_bytes,
    content_version,
    decode_text,
)


def clean_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or any(part in ("..", ".git") for part in path.split("/"))
        or "\\" in path
    ):
        raise ValidationError("文件路径必须在任务工作目录内")
    return path


class ProjectFiles:
    def __init__(
        self, session: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID | None
    ):
        self.session, self.project_id, self.task_id = session, project_id, task_id

    async def task(self):
        task = await self.session.get(Task, self.task_id) if self.task_id else None
        if self.task_id and (task is None or task.project_id != self.project_id):
            raise NotFoundError("这个项目没有此任务")
        return task

    async def source(self, requested: str):
        task = await self.task()
        return (
            "live"
            if requested == "live" and task and task.status == TaskStatus.open
            else "committed"
        )

    async def live(self, operation: str, **params):
        task = await self.task()
        if task is None:
            raise ValidationError("请选择任务")
        room = await self.session.get(Topic, task.room_id)
        # Executors are pinned per room; sessions record their leases separately.
        target = next(
            (
                place.lease
                for place in await AgentSessionService(self.session).places_in_room(
                    task.room_id
                )
                if room is not None
                and place.resource_id == str(room.resource_id or room.id)
                and (place.lease or {}).get("kind") == "device"
            ),
            None,
        )
        if not target or target.get("kind") != "device":
            raise GatewayUnavailableError("任务机器尚未连接；可以查看已提交版本")
        try:
            # Interactive file requests must not inherit the agent's 11-minute
            # command timeout; cancellation also cancels the pending device call.
            async with asyncio.timeout(30):
                result = await execution.call(
                    target,
                    "task_fs",
                    {"task_id": str(task.id), "operation": operation, **params},
                )
        except (TimeoutError, DeviceOffline, DeviceNotReady) as exc:
            raise GatewayUnavailableError(
                "任务机器暂时无法响应，请重新读取文件后再操作；也可以查看已提交版本"
            ) from exc
        if result.get("error") == "not_found":
            raise NotFoundError("机器上还没有这个任务文件")
        if result.get("error") == "conflict":
            raise ConflictError("文件已有更新，请重新读取后再保存")
        return result

    async def revision(self):
        task = await self.task()
        if task and task.delivered_head:
            return task.delivered_head
        branch = (
            task.branch_name
            if task
            else await default_branch(self.project_id, self.session)
        )
        if not branch:
            raise NotFoundError("任务没有独立的文件版本")
        head = await branch_head(self.project_id, self.session, branch)
        if not head:
            raise NotFoundError("这个任务还没有提交文件")
        return head

    async def files(self, source: str):
        source = await self.source(source)
        if source == "live":
            return (await self.live("tree"))["files"], source
        head = await self.revision()
        entries = await self.committed_entries(head)
        return [
            {"path": entry["path"], "bytes": entry["bytes"]}
            for entry in entries
            if entry["kind"] == "blob"
        ], source

    async def committed_entries(self, revision: str):
        """List immutable entries, retaining unsafe modes for release validation."""
        pending = [("", revision)]
        files = []
        while pending:
            prefix, sha = pending.pop()
            for entry in await self._tree(sha):
                name = prefix + entry["path"]
                if entry["type"] == "tree":
                    pending.append((name + "/", entry["sha"]))
                else:
                    files.append(
                        {
                            "path": name,
                            "bytes": entry.get("size", 0),
                            "kind": entry["type"],
                            "mode": entry["mode"],
                            "oid": entry["sha"],
                        }
                    )
        return sorted(files, key=lambda f: f["path"])

    async def committed_blobs(self, oids: list[str]):
        return {oid: await self._blob({"sha": oid}) for oid in dict.fromkeys(oids)}

    async def compare_revisions(self, before: str, after: str) -> list[dict]:
        """Compare two delivered trees, never a moving branch or PR merge-base."""
        old = {entry["path"]: entry for entry in await self.committed_entries(before)}
        new = {entry["path"]: entry for entry in await self.committed_entries(after)}
        changes = []
        for path in sorted(old.keys() | new.keys()):
            left, right = old.get(path), new.get(path)
            if left == right:
                continue
            entries = [entry for entry in (left, right) if entry]
            result = {
                "identical": False,
                "diff": None,
                "note": "unsupported",
            }
            if all(
                entry["kind"] == "blob" and entry["bytes"] <= MAX_TEXT_BYTES
                for entry in entries
            ):
                blobs = await self.committed_blobs([entry["oid"] for entry in entries])
                result = await asyncio.to_thread(
                    compare_bytes,
                    blobs[left["oid"]] if left else b"",
                    blobs[right["oid"]] if right else b"",
                    path if left else "/dev/null",
                    path if right else "/dev/null",
                )
                if result["diff"] is None:
                    result["note"] = "unsupported"
            changes.append(
                {
                    "path": path,
                    "status": "added"
                    if left is None
                    else "removed"
                    if right is None
                    else "modified",
                    "before_mode": left["mode"] if left else None,
                    "after_mode": right["mode"] if right else None,
                    **result,
                }
            )
        return changes

    async def comparison(self):
        task = await self.task()
        if task is None or not task.branch_name:
            return None
        head = task.delivered_head or await branch_head(
            self.project_id, self.session, task.branch_name
        )
        if not head:
            return None  # The task has not pushed its first commit yet.
        base = task.base_branch or await default_branch(self.project_id, self.session)
        comparison = await repository_data(
            self.project_id,
            self.session,
            f"/compare/{quote(base, safe='')}...{quote(head, safe='')}",
        )
        if comparison is None:
            raise NotFoundError("任务的对比版本不存在")
        if len(comparison.get("files") or []) >= 300:
            binding = await binding_for_project(self.project_id, self.session)
            if binding is not None and binding.kind == "github_app":
                # GitHub caps compare.files at 300, including paginated replies.
                # Compare against the merge base, as the PR's three-dot diff does.
                ancestor = comparison["merge_base_commit"]["sha"]
                before = {
                    entry["path"]: (entry["oid"], entry["mode"])
                    for entry in await self.committed_entries(ancestor)
                }
                after = {
                    entry["path"]: (entry["oid"], entry["mode"])
                    for entry in await self.committed_entries(head)
                }
                comparison["files"] = [
                    {
                        "filename": path,
                        "status": (
                            "added"
                            if path not in before
                            else "removed"
                            if path not in after
                            else "modified"
                        ),
                    }
                    for path in sorted(before.keys() | after.keys())
                    if before.get(path) != after.get(path)
                ]
        return comparison

    async def history(self):
        if self.task_id:
            comparison = await self.comparison()
            commits = (comparison or {}).get("commits", [])
        else:
            head = await self.revision()
            commits = (
                await repository_data(
                    self.project_id,
                    self.session,
                    f"/commits?sha={head}&per_page=50&limit=50",
                )
                or []
            )
        return [
            {
                "hash": item["sha"][:12],
                "sha": item["sha"],
                "author": item["commit"]["author"]["name"],
                "message": item["commit"]["message"].splitlines()[0],
            }
            for item in commits
        ]

    async def changed_files(self):
        comparison = await self.comparison()
        if comparison is None:
            return []
        if comparison.get("files") is None:
            raise GatewayUnavailableError("代码托管服务未返回改动文件列表")
        return [entry["filename"] for entry in comparison["files"]]

    async def diff(self, source: str, ref: str | None = None):
        if ref and ref.startswith("-"):
            raise ValidationError("提交版本无效")
        task = await self.task()
        if await self.source(source) == "live":
            assert task is not None  # Only open tasks have a live source.
            base = task.base_branch or await default_branch(
                self.project_id, self.session
            )
            return (await self.live("diff", base_branch=base))["diff"]
        binding = await binding_for_project(self.project_id, self.session)
        if binding is None:
            raise NotFoundError("项目没有代码仓库")
        if task:
            if not task.pr_number:
                if not task.branch_name or not await branch_head(
                    self.project_id, self.session, task.branch_name
                ):
                    return ""
                raise GatewayUnavailableError(
                    "已提交版本的评审记录尚未创建，请稍后刷新"
                )
            path = f"/pulls/{task.pr_number}" + (
                ".diff" if binding.kind == "forgejo" else ""
            )
        else:
            head = await self.revision()
            if ref and ref != head:
                tokens = await tokens_for_project(self.project_id, self.session)
                if tokens is None:
                    raise GatewayUnavailableError("项目的代码托管凭据尚未配置")
                token, _ = await tokens.installation_token()
                owner, repo = binding.repo.split("/", 1)
                client = await status_client(self.project_id, self.session)
                relation = await client.compare_status(
                    owner=owner, repo=repo, base=ref, head=head, token=token
                )
                if relation not in ("ahead", "identical"):
                    raise NotFoundError("此提交不在项目已交付的历史中")
            return await self.commit_diff(ref or head)
        data = await repository_data(self.project_id, self.session, path, diff=True)
        if data is None:
            raise NotFoundError("此版本的差异不存在")
        return data

    async def commit_diff(self, revision: str):
        binding = await binding_for_project(self.project_id, self.session)
        if binding is None:
            raise NotFoundError("项目没有代码仓库")
        revision = quote(revision, safe="")
        path = (
            f"/git/commits/{revision}.diff"
            if binding.kind == "forgejo"
            else f"/commits/{revision}"
        )
        data = await repository_data(self.project_id, self.session, path, diff=True)
        if data is None:
            raise NotFoundError("此版本的差异不存在")
        return data

    async def _tree(self, sha):
        entries, page_number = [], 1
        while True:
            data = await repository_data(
                self.project_id,
                self.session,
                f"/git/trees/{quote(sha, safe='')}?per_page=500&page={page_number}",
            )
            if data is None:
                raise NotFoundError("已提交的目录不存在")
            entries.extend(data["tree"])
            if not data.get("truncated"):
                return entries
            if "page" not in data or not data["tree"]:
                raise GatewayUnavailableError("代码托管服务未返回完整目录")
            page_number += 1

    async def _entry(self, path, revision):
        parts = clean_path(path).split("/")
        sha = revision
        for index, part in enumerate(parts):
            entry = next(
                (item for item in await self._tree(sha) if item["path"] == part), None
            )
            if entry is None:
                raise NotFoundError("已提交版本中没有这个文件")
            if index == len(parts) - 1:
                if entry["type"] != "blob" or entry.get("mode") == "120000":
                    raise NotFoundError("这个路径不是可读取的文件")
                return entry
            if entry["type"] != "tree":
                raise NotFoundError("文件所在目录不存在")
            sha = entry["sha"]
        raise NotFoundError("已提交版本中没有这个文件")

    async def _blob(self, entry):
        data = await repository_data(
            self.project_id, self.session, f"/git/blobs/{entry['sha']}"
        )
        if not data or data.get("encoding") != "base64":
            raise GatewayUnavailableError("代码托管服务没有返回文件内容")
        return base64.b64decode(data["content"])

    async def raw(self, path: str, source: str):
        path = clean_path(path)
        source = await self.source(source)
        if source == "live":
            first = await self.live("read", path=path, size=2 * 1024 * 1024)
            chunks = [base64.b64decode(first["data"])]
            offset = len(chunks[0])
            while offset < first["bytes"]:
                part = await self.live(
                    "read",
                    path=path,
                    offset=offset,
                    size=2 * 1024 * 1024,
                    version=first["version"],
                )
                chunk = base64.b64decode(part["data"])
                if not chunk:
                    raise ConflictError("文件在读取期间变化，请重试")
                chunks.append(chunk)
                offset += len(chunk)
            return b"".join(chunks), source
        head = await self.revision()
        return await self._blob(await self._entry(path, head)), source

    async def text(self, path: str, source: str):
        path = clean_path(path)
        source = await self.source(source)
        if source == "live":
            read = await self.live("read", path=path, size=MAX_TEXT_BYTES)
            size, version = read["bytes"], read["version"]
            data = base64.b64decode(read["data"])
        else:
            entry = await self._entry(path, await self.revision())
            size = entry.get("size", 0)
            data = await self._blob(entry) if size <= MAX_TEXT_BYTES else b""
            version = content_version(data) if size <= MAX_TEXT_BYTES else None
        large = size > MAX_TEXT_BYTES
        text = None if large else decode_text(data)
        return {
            "path": path,
            "bytes": size,
            "content": text,
            "binary": not large and text is None,
            "too_large": large,
            "version": version,
            "source": source,
            "editable": source == "live" and not large and text is not None,
        }

    async def write(self, path: str, content: str, version: str | None):
        task = await self.task()
        if task is None or task.status != TaskStatus.open:
            raise ValidationError("任务已经结束，文件只读")
        if not version:
            raise ConflictError("请先读取文件，再保存修改")
        return await self.live(
            "write", path=clean_path(path), content=content, version=version
        )

    async def write_bytes(self, path: str, data: bytes, version: str):
        task = await self.task()
        if task is None or task.status != TaskStatus.open:
            raise ValidationError("任务已经结束，文件只读")
        return await self.live(
            "write_bytes",
            path=clean_path(path),
            data=base64.b64encode(data).decode(),
            version=version,
        )
