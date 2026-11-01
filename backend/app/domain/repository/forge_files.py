"""Read committed files from the forge and unfinished files from their executor."""

import asyncio
import base64
import json
import uuid
from typing import Any
from urllib.parse import quote

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.db import release_read_session
from app.core.device_failures import DeviceCallError, DeviceNotReady, DeviceOffline
from app.core.errors import (
    ConflictError,
    GatewayUnavailableError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.forge import (
    binding_for_project,
    branch_head,
    default_branch,
    repository_data,
)
from app.domain.repository import merge3, task_rows
from app.domain.repository.ports import CompareStatus, ExecutorCall
from app.domain.textfile import (
    MAX_TEXT_BYTES,
    PREVIEW_TEXT_BYTES,
    compare_bytes,
    content_version,
    decode_text,
    head_text,
)
from app.domain.topic.models import Topic

#: How many changed files one comparison reads for a line diff.
MAX_DIFFED_FILES = 200
#: How many blob reads one comparison keeps in the air at the same time.
MAX_BLOB_READS = 8
#: The longest listing every committed file of one revision may take.
TREE_DEADLINE_S = 30


def _file_entry(path: str, entry: dict) -> dict:
    return {
        "path": path,
        "bytes": entry.get("size", 0),
        "kind": entry["type"],
        "mode": entry["mode"],
        "oid": entry["sha"],
    }


def clean_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or any(part in ("..", ".git") for part in path.split("/"))
        or "\\" in path
    ):
        raise ValidationError(say("taskFilePathOutside"))
    return path


#: How long one head's changed paths are kept. The comparison is three-dot,
#: from the merge base, so it moves only when the head does; the expiry covers
#: the base branch being merged into the task branch's history some other way.
_CHANGED_FILES_TTL = 6 * 3600


def _changed_files_key(project_id: uuid.UUID, base: str, head: str) -> str:
    return f"cheesex:changed-files:{project_id}:{base}:{head}"


def _landed_head(task: Any) -> str | None:
    """The head the task's landed delivery left, while that is still the task's
    code: the task closed on it, or is being written up after its last step.

    A task that goes on after a step lands works on a new branch from the latest
    code (`TaskService.next_step`), so the landed head is the step before it,
    already on the default branch, and its files are not the open step's."""
    if not task_rows.is_open(task) or task.closing_since is not None:
        return task.delivered_head
    return None


#: The operations that ask for a task's whole working copy rather than one
#: file in it. Their ``not_found`` can only mean the task's directory is
#: missing, which is all an executor installed before ``not_found`` carried
#: ``missing`` can say: it answers a file operation the same way whether the
#: directory or the file is gone, and that answer stays "not on the machine".
_WHOLE_COPY = ("tree", "diff")


class _NoWorkingCopy(NotFoundError):
    """No machine holds the task's working copy and its branch is pushed: one
    file is read from the branch, and nothing can be written to it."""


class ProjectFiles:
    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        task_id: uuid.UUID | None,
        *,
        release_session: bool = False,
        executor: ExecutorCall | None = None,
        compare_status: CompareStatus | None = None,
    ):
        """``executor`` 是在任务那台机器上跑一次文件操作的东西（`ports.ExecutorCall`）；
        ``compare_status`` 是问托管方「这个提交交付过没有」那一次调用
        （`ports.CompareStatus`）。

        谁构造谁给：只有走进 ``live()`` 的那几条路要 ``executor``，只有点名比一个
        提交和当前版本（``diff`` 带 ``ref``）的那条路要 ``compare_status``，其余不接线，
        ``None`` 就是不给。真的走到要它的那条路上还是没有它，就是有个门漏了，当场炸掉，
        不会静默跳过这一次检查。
        """
        self.session, self.project_id, self.task_id = session, project_id, task_id
        self.release_session = release_session
        self._executor = executor
        self._compare_status = compare_status

    async def _data(self, *args, **kwargs):
        if self.release_session:
            kwargs["release_session"] = True
        return await repository_data(*args, **kwargs)

    async def _head(self, *args, **kwargs):
        if self.release_session:
            kwargs["release_session"] = True
        return await branch_head(*args, **kwargs)

    async def _release(self):
        if self.release_session:
            await release_read_session(self.session)

    async def task(self):
        """The task's row, or None when this view is not about one task.

        Read as columns of the `tasks` table (`task_rows`): `room_task` is a
        layer above this package.
        """
        if not self.task_id:
            return None
        task = await task_rows.one(self.session, self.task_id)
        if task is None or task.project_id != self.project_id:
            raise NotFoundError(say("projectTaskNotFound"))
        return task

    async def source(self, requested: str):
        task = await self.task()
        if requested != "live" or task is None or not task_rows.is_open(task):
            return "committed"
        if await self._working_copy(task) is None and await self._pushed(task):
            # No machine holds a working copy of it, and the next one starts
            # from the branch (`cheese worktree`): its files are the committed
            # ones, and they are read, not edited, until a machine has them.
            return "committed"
        return "live"

    async def _working_copy(self, task: Any):
        room = await self.session.get(Topic, task.room_id)
        if room is None:
            return None
        return await AgentSessionService(self.session).working_copy(
            task.id, str(room.resource_id or room.id)
        )

    async def _pushed(self, task: Any) -> bool:
        """Whether the task has a version on the forge to start a copy from."""
        return bool(
            _landed_head(task)
            or (
                task.branch_name
                and await self._head(self.project_id, self.session, task.branch_name)
            )
        )

    async def _without_working_copy(self, task: Any, operation: str) -> dict[str, Any]:
        """The task's files and changes while no machine holds them: what a new
        working copy would start from, its branch. A task that has pushed
        nothing yet has no files and no changes of its own."""
        if not await self._pushed(task):
            if operation not in _WHOLE_COPY:
                raise NotFoundError(say("taskFileNotOnMachine"))
            return {"tree": {"files": []}, "diff": {"diff": ""}}[operation]
        if operation == "tree":
            return {"files": (await self.files("committed"))[0]}
        if operation == "diff":
            return {"diff": await self.diff("committed")}
        raise _NoWorkingCopy(say("taskFileNotOnMachine"))

    async def live(self, operation: str, **params):
        task = await self.task()
        if task is None:
            raise ValidationError(say("taskRequired"))
        found = await self._working_copy(task)
        if found is None:
            # No session of this task has taken a machine yet, so no machine
            # holds a working copy of it: the same as a machine without one.
            return await self._without_working_copy(task, operation)
        target, lost = found
        if lost:
            # Its cloud sandbox was destroyed with its home (idle, or given up
            # with its host), and the lease still names it until the task's
            # next tool call places it in a new one.
            raise UpstreamUnavailableError(say("taskSandboxReleased"))
        if self._executor is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("executor was not handed in")
        await self._release()
        try:
            # Interactive file requests must not inherit the agent's 11-minute
            # command timeout; cancellation also cancels the pending device call.
            async with asyncio.timeout(30):
                result = await self._executor(
                    target,
                    "task_fs",
                    {"task_id": str(task.id), "operation": operation, **params},
                )
        except (TimeoutError, DeviceOffline, DeviceNotReady) as exc:
            raise GatewayUnavailableError(say("taskMachineNotResponding")) from exc
        except DeviceCallError as exc:
            raise UpstreamUnavailableError(
                say("taskMachineFailed", error=str(exc))
            ) from exc
        if result.get("error") == "not_found":
            if result.get("missing") == "worktree" or operation in _WHOLE_COPY:
                return await self._without_working_copy(task, operation)
            raise NotFoundError(say("taskFileNotOnMachine"))
        if result.get("error") == "conflict":
            raise ConflictError(say("taskFileChangedReload"))
        return result

    async def revision(self):
        task = await self.task()
        landed = _landed_head(task) if task else None
        if landed:
            return landed
        branch = (
            task.branch_name
            if task
            else await default_branch(self.project_id, self.session)
        )
        if not branch:
            raise NotFoundError(say("taskNoOwnFileVersion"))
        head = await self._head(self.project_id, self.session, branch)
        if not head:
            raise NotFoundError(say("taskNothingCommitted"))
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
        """List immutable entries, retaining unsafe modes for release validation.

        One recursive tree read (paged on Forgejo) rather than one request per
        directory, and the whole listing has one deadline: a page waiting on
        it is a person waiting on the file panel or the site settings."""
        try:
            async with asyncio.timeout(TREE_DEADLINE_S):
                entries = await self._tree(revision, recursive=True)
        except TimeoutError as exc:
            raise GatewayUnavailableError(say("forgeTimeout")) from exc
        return sorted(
            (_file_entry(e["path"], e) for e in entries if e["type"] != "tree"),
            key=lambda f: f["path"],
        )

    async def _changed_entries(
        self, before: str, after: str
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        """The files that differ between two commits, as each side has them.

        The two trees are walked together and a directory is only opened when
        its id differs on the two sides: the same id means the same files, so a
        comparison costs the directories that changed, not the whole repository.
        """
        old: dict[str, dict] = {}
        new: dict[str, dict] = {}
        pending: list[tuple[str, str | None, str | None]] = [("", before, after)]
        while pending:
            prefix, left_sha, right_sha = pending.pop()
            left = (
                {e["path"]: e for e in await self._tree(left_sha)} if left_sha else {}
            )
            right = (
                {e["path"]: e for e in await self._tree(right_sha)} if right_sha else {}
            )
            for name in left.keys() | right.keys():
                was, now = left.get(name), right.get(name)
                if (
                    was
                    and now
                    and was["sha"] == now["sha"]
                    and was["mode"] == now["mode"]
                ):
                    continue
                path = prefix + name
                was_dir = was["sha"] if was and was["type"] == "tree" else None
                now_dir = now["sha"] if now and now["type"] == "tree" else None
                if was_dir or now_dir:
                    pending.append((path + "/", was_dir, now_dir))
                if was and not was_dir:
                    old[path] = _file_entry(path, was)
                if now and not now_dir:
                    new[path] = _file_entry(path, now)
        return old, new

    async def committed_blobs(self, oids: list[str]):
        """Read many blobs at once, each read on a session of its own.

        One `AsyncSession` runs one statement at a time, and every read goes
        through `repository_data`, which looks the project's binding and its
        tokens up in the session it is handed (`project/forge.py`) — so reads
        running together cannot share one. Each takes a session minted off this
        one's engine, not the module-level factory, which the test harness binds
        to another database (`review/services/polling.py` does the same).
        `MAX_BLOB_READS` of them are in the air at a time; the answer holds
        every oid asked for, as reading them one by one did.

        The reads are collected in the order they were asked for, so a failure
        reaches the caller where reading one file after another would have put
        it; the reads still in the air are then cancelled, rather than left
        asking the forge after the call that wanted them has already failed."""
        unique = list(dict.fromkeys(oids))
        if not unique:
            return {}
        sessions = async_sessionmaker(self.session.bind, expire_on_commit=False)
        at_once = asyncio.Semaphore(MAX_BLOB_READS)

        async def read(oid: str) -> bytes:
            async with at_once, sessions() as session:
                return await self._blob({"sha": oid}, session)

        running = [asyncio.create_task(read(oid)) for oid in unique]
        try:
            return dict(zip(unique, [await task for task in running], strict=True))
        finally:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)

    async def compare_revisions(self, before: str, after: str) -> list[dict]:
        """Compare two delivered trees, never a moving branch or PR merge-base.

        Only the first `MAX_DIFFED_FILES` changed files are read for a line
        diff; the rest are listed as changed. Two deliveries far apart in a
        large repository differ in thousands of files, each one a request, so
        the reads go out together, `MAX_BLOB_READS` at a time, rather than one
        file after another — the first pass decides every file's lot in listing
        order without reading anything, and only then are the blobs the diffed
        files share asked for."""
        old, new = await self._changed_entries(before, after)
        listed: list[tuple[str, dict | None, dict | None, str | None]] = []
        diffed = 0
        for path in sorted(old.keys() | new.keys()):
            left, right = old.get(path), new.get(path)
            if left == right:
                continue
            entries = [entry for entry in (left, right) if entry]
            note = None
            if diffed >= MAX_DIFFED_FILES:
                # Past the cap, a file is listed as `many` whether or not it
                # could have been diffed at all.
                note = "many"
            elif all(
                entry["kind"] == "blob" and entry["bytes"] <= MAX_TEXT_BYTES
                for entry in entries
            ):
                diffed += 1
            else:
                note = "unsupported"
            listed.append((path, left, right, note))
        blobs = await self.committed_blobs(
            [
                entry["oid"]
                for _, left, right, note in listed
                if note is None
                for entry in (left, right)
                if entry
            ]
        )
        changes = []
        for path, left, right, note in listed:
            if note is None:
                result = await asyncio.to_thread(
                    compare_bytes,
                    blobs[left["oid"]] if left else b"",
                    blobs[right["oid"]] if right else b"",
                    path if left else "/dev/null",
                    path if right else "/dev/null",
                )
                if result["diff"] is None:
                    result["note"] = "unsupported"
            else:
                result = {"identical": False, "diff": None, "note": note}
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

    async def _refs(self) -> tuple[str, str] | None:
        """The base branch and head commit the task's changes are read between,
        or None while the task has no pushed commit."""
        task = await self.task()
        if task is None or not task.branch_name:
            return None
        head = _landed_head(task) or await self._head(
            self.project_id, self.session, task.branch_name
        )
        if not head:
            return None  # The task has not pushed its first commit yet.
        base = task.base_branch or await default_branch(self.project_id, self.session)
        return base, head

    async def comparison(self, refs: tuple[str, str] | None = None):
        refs = refs or await self._refs()
        if refs is None:
            return None
        base, head = refs
        comparison = await self._data(
            self.project_id,
            self.session,
            f"/compare/{quote(base, safe='')}...{quote(head, safe='')}",
        )
        if comparison is None:
            raise NotFoundError(say("taskBaseVersionMissing"))
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
                await self._data(
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
        """The task's changed paths. A head commit's changes against its base
        stay what they were, so they are kept by commit (`_CHANGED_FILES_TTL`)
        and an unchanged branch costs one head lookup instead of a comparison."""
        refs = await self._refs()
        if refs is None:
            return []
        key = _changed_files_key(self.project_id, *refs)
        redis = get_redis_client()
        if redis is not None:
            # The comparison is the answer; a cache that is slow, down or holds
            # something this code did not write is a miss, never the page's
            # error. `request_limit_redis_timeout_s` is the request path's bound.
            try:
                cached = await asyncio.wait_for(
                    redis.get(key),
                    timeout=settings.request_limit_redis_timeout_s,
                )
            except (RedisError, TimeoutError):
                cached = None
            if cached is not None:
                try:
                    return json.loads(cached)
                except (TypeError, ValueError):
                    pass  # not a value this code wrote: compare instead
        comparison = await self.comparison(refs)
        if comparison is None:
            return []
        if comparison.get("files") is None:
            raise GatewayUnavailableError(say("forgeNoChangedFiles"))
        paths = [entry["filename"] for entry in comparison["files"]]
        if redis is not None:
            try:
                await asyncio.wait_for(
                    redis.set(key, json.dumps(paths), ex=_CHANGED_FILES_TTL),
                    timeout=settings.request_limit_redis_timeout_s,
                )
            except (RedisError, TimeoutError):
                pass
        return paths

    async def diff(self, source: str, ref: str | None = None):
        if ref and ref.startswith("-"):
            raise ValidationError(say("commitInvalid"))
        task = await self.task()
        if await self.source(source) == "live":
            assert task is not None  # Only open tasks have a live source.
            base = task.base_branch or await default_branch(
                self.project_id, self.session
            )
            return (await self.live("diff", base_branch=base))["diff"]
        binding = await binding_for_project(self.project_id, self.session)
        if binding is None:
            raise NotFoundError(say("forgeNoRepository"))
        if task:
            if not task.pr_number:
                if not task.branch_name or not await self._head(
                    self.project_id, self.session, task.branch_name
                ):
                    return ""
                raise GatewayUnavailableError(say("committedReviewNotReady"))
            path = f"/pulls/{task.pr_number}" + (
                ".diff" if binding.kind == "forgejo" else ""
            )
        else:
            head = await self.revision()
            if ref and ref != head:
                if self._compare_status is None:  # pragma: no cover - 接线漏了才会走到
                    # i18n-exempt: developer miswiring, not a readable refusal
                    raise RuntimeError("compare_status was not handed in")
                relation = await self._compare_status(
                    self.project_id,
                    self.session,
                    base=ref,
                    head=head,
                    release_session=self.release_session,
                )
                if relation not in ("ahead", "identical"):
                    raise NotFoundError(say("commitNotDelivered"))
            return await self.commit_diff(ref or head)
        data = await self._data(self.project_id, self.session, path, diff=True)
        if data is None:
            raise NotFoundError(say("revisionDiffNotFound"))
        return data

    async def commit_diff(self, revision: str):
        binding = await binding_for_project(self.project_id, self.session)
        if binding is None:
            raise NotFoundError(say("forgeNoRepository"))
        revision = quote(revision, safe="")
        path = (
            f"/git/commits/{revision}.diff"
            if binding.kind == "forgejo"
            else f"/commits/{revision}"
        )
        data = await self._data(self.project_id, self.session, path, diff=True)
        if data is None:
            raise NotFoundError(say("revisionDiffNotFound"))
        return data

    async def _tree(self, sha, *, recursive: bool = False):
        entries, page_number = [], 1
        # GitHub answers a recursive tree in one response (`truncated` past its
        # size limit, which is raised below); Forgejo pages it like any tree.
        recurse = "&recursive=1" if recursive else ""
        while True:
            data = await self._data(
                self.project_id,
                self.session,
                f"/git/trees/{quote(sha, safe='')}?per_page=500&page={page_number}"
                + recurse,
            )
            if data is None:
                raise NotFoundError(say("committedDirNotFound"))
            # Forgejo serializes an empty tree as null.
            entries.extend(data["tree"] or [])
            if not data.get("truncated"):
                return entries
            if "page" not in data or not data["tree"]:
                raise GatewayUnavailableError(say("forgeIncompleteDirectory"))
            page_number += 1

    async def _entry(self, path, revision):
        parts = clean_path(path).split("/")
        sha = revision
        for index, part in enumerate(parts):
            entry = next(
                (item for item in await self._tree(sha) if item["path"] == part), None
            )
            if entry is None:
                raise NotFoundError(say("committedFileNotFound"))
            if index == len(parts) - 1:
                if entry["type"] != "blob" or entry.get("mode") == "120000":
                    raise NotFoundError(say("pathNotReadableFile"))
                return entry
            if entry["type"] != "tree":
                raise NotFoundError(say("fileDirectoryNotFound"))
            sha = entry["sha"]
        raise NotFoundError(say("committedFileNotFound"))

    async def _blob(self, entry, session):
        """The blob's bytes, read on `session`.

        The caller passes the session to read on: one blob read at a time is
        this object's own (`self.session`), while reads that go out together
        each take one of their own (`committed_blobs`)."""
        data = await self._data(self.project_id, session, f"/git/blobs/{entry['sha']}")
        if not data or data.get("encoding") != "base64":
            raise GatewayUnavailableError(say("forgeNoFileContent"))
        return base64.b64decode(data["content"])

    async def raw(self, path: str, source: str):
        path = clean_path(path)
        source = await self.source(source)
        if source == "live":
            try:
                return await self._live_bytes(path), source
            except _NoWorkingCopy:
                source = "committed"
        head = await self.revision()
        return await self._blob(await self._entry(path, head), self.session), source

    async def _live_bytes(self, path: str) -> bytes:
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
                raise ConflictError(say("fileChangedWhileReading"))
            chunks.append(chunk)
            offset += len(chunk)
        return b"".join(chunks)

    async def raw_at(self, path: str, revision: str) -> bytes | None:
        """The file as one commit has it; None when that commit has no such file."""
        try:
            entry = await self._entry(clean_path(path), revision)
        except NotFoundError:
            return None
        return await self._blob(entry, self.session)

    async def base_revision(self) -> str | None:
        """The commit this task's work started from: the merge base with the
        branch it goes into, which is the last version taken in."""
        comparison = await self.comparison()
        if not comparison:
            return None
        return (comparison.get("merge_base_commit") or {}).get("sha")

    async def text(self, path: str, source: str):
        path = clean_path(path)
        source = await self.source(source)
        read = None
        if source == "live":
            try:
                read = await self.live("read", path=path, size=MAX_TEXT_BYTES)
            except _NoWorkingCopy:
                source = "committed"
        if read is not None:
            size, version = read["bytes"], read["version"]
            data = base64.b64decode(read["data"])
        else:
            entry = await self._entry(path, await self.revision())
            size = entry.get("size", 0)
            data = (
                await self._blob(entry, self.session) if size <= MAX_TEXT_BYTES else b""
            )
            version = content_version(data) if size <= MAX_TEXT_BYTES else None
        large = size > MAX_TEXT_BYTES
        text = None if large else decode_text(data)
        if not large:
            head = text
        elif data:
            # live 那一支读的是前 `MAX_TEXT_BYTES`，手里有开头，给得出来。
            head = head_text(data[:PREVIEW_TEXT_BYTES])
        else:
            # committed 那一支超上限时一个字节都没读（不为了开头把整份拉回来），
            # 那就没有开头可给：空串会被读者当成「是文本、但一个字都没有」。
            head = None
        return {
            "path": path,
            "bytes": size,
            "content": text,
            # 读者看的那一份：整份，或者开头（见 `textfile.text_payload`）。这一格
            # 用的是编辑器，读的是 `content`；`head` 只为同一份契约在别处一致。
            "head": head,
            "binary": not large and text is None,
            "too_large": large,
            "version": version,
            "source": source,
            "editable": source == "live" and not large and text is not None,
        }

    async def write(self, path: str, content: str, version: str | None):
        task = await self.task()
        if task is None or not task_rows.is_open(task):
            raise ValidationError(say("taskFinishedFilesReadOnly"))
        if not version:
            raise ConflictError(say("taskFileReadBeforeSave"))
        return await self.live(
            "write", path=clean_path(path), content=content, version=version
        )

    async def save(
        self, path: str, content: str, version: str | None, base: str | None
    ) -> dict:
        """A person's save, merged with whatever changed the file since they read it.

        `base` is the text they read, the one `version` names. When the file
        moved on in between, their edits and the other ones are merged against
        it: a clean merge is written, overlapping edits come back as a 409
        carrying the regions to pick from and the version to save over. Without
        a `base` (or one that is not the text `version` names) there is nothing
        to merge against, and the save is refused as before. Answers the saved
        version and the text it replaced.
        """
        try:
            saved = await self.write(path, content, version)
            return {**saved, "previous": base, "merged": False}
        except ConflictError:
            if base is None or content_version(base.encode()) != version:
                raise
        now = await self.text(path, "live")
        theirs, theirs_version = now.get("content"), now.get("version")
        if theirs is None:
            raise ConflictError(say("taskFileChangedReload"))
        regions = merge3.merge(base, content, theirs)
        merged = merge3.merged_text(regions)
        if merged is None:
            raise ConflictError(
                say("taskFileEditsOverlap"),
                data={
                    "regions": [region.as_dict() for region in regions],
                    "version": theirs_version,
                    "base": theirs,
                },
            )
        saved = await self.write(path, merged, theirs_version)
        return {**saved, "previous": theirs, "merged": True, "content": merged}

    async def write_bytes(self, path: str, data: bytes, version: str):
        task = await self.task()
        if task is None or not task_rows.is_open(task):
            raise ValidationError(say("taskFinishedFilesReadOnly"))
        return await self.live(
            "write_bytes",
            path=clean_path(path),
            data=base64.b64encode(data).decode(),
            version=version,
        )
