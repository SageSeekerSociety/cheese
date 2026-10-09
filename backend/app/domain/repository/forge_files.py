"""Read committed files from the forge and unfinished files from their executor."""

import asyncio
import base64
import uuid
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import release_read_session
from app.core.errors import (
    ConflictError,
    GatewayUnavailableError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceCallError, DeviceNotReady, DeviceOffline
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.forge import (
    binding_for_project,
    branch_head,
    default_branch,
    repository_data,
    status_client,
    tokens_for_project,
)
from app.domain.repository import merge3
from app.domain.room_task.models import Task, TaskStatus
from app.domain.textfile import (
    MAX_TEXT_BYTES,
    compare_bytes,
    content_version,
    decode_text,
)
from app.domain.topic.models import Topic

#: How many changed files one comparison reads for a line diff.
MAX_DIFFED_FILES = 200
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


def _landed_head(task: Task) -> str | None:
    """The head the task's landed delivery left, while that is still the task's
    code: the task closed on it, or is being written up after its last step.

    A task that goes on after a step lands works on a new branch from the latest
    code (`TaskService.next_step`), so the landed head is the step before it,
    already on the default branch, and its files are not the open step's."""
    if task.status != TaskStatus.open or task.closing_since is not None:
        return task.delivered_head
    return None


class ProjectFiles:
    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        task_id: uuid.UUID | None,
        *,
        release_session: bool = False,
    ):
        self.session, self.project_id, self.task_id = session, project_id, task_id
        self.release_session = release_session

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
        task = await self.session.get(Task, self.task_id) if self.task_id else None
        if self.task_id and (task is None or task.project_id != self.project_id):
            raise NotFoundError(say("projectTaskNotFound"))
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
            raise ValidationError(say("taskRequired"))
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
            raise GatewayUnavailableError(say("taskMachineNotConnected"))
        await self._release()
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
            raise GatewayUnavailableError(say("taskMachineNotResponding")) from exc
        except DeviceCallError as exc:
            # A cloud sandbox left idle is destroyed with its home, and the
            # room's lease still names it until the next tool call places the
            # session again. The machine's own words for that are a missing
            # path; say what happened instead.
            if await AgentSessionService(self.session).sandbox_lost_in_room(
                task.room_id
            ):
                raise UpstreamUnavailableError(say("taskSandboxReleased")) from exc
            raise
        if result.get("error") == "not_found":
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
        return {oid: await self._blob({"sha": oid}) for oid in dict.fromkeys(oids)}

    async def compare_revisions(self, before: str, after: str) -> list[dict]:
        """Compare two delivered trees, never a moving branch or PR merge-base.

        Only the first `MAX_DIFFED_FILES` changed files are read for a line
        diff; the rest are listed as changed. Two deliveries far apart in a
        large repository differ in thousands of files, each one a request."""
        old, new = await self._changed_entries(before, after)
        changes = []
        diffed = 0
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
            if diffed >= MAX_DIFFED_FILES:
                result["note"] = "many"
            elif all(
                entry["kind"] == "blob" and entry["bytes"] <= MAX_TEXT_BYTES
                for entry in entries
            ):
                diffed += 1
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
        head = _landed_head(task) or await self._head(
            self.project_id, self.session, task.branch_name
        )
        if not head:
            return None  # The task has not pushed its first commit yet.
        base = task.base_branch or await default_branch(self.project_id, self.session)
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
        comparison = await self.comparison()
        if comparison is None:
            return []
        if comparison.get("files") is None:
            raise GatewayUnavailableError(say("forgeNoChangedFiles"))
        return [entry["filename"] for entry in comparison["files"]]

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
                tokens = await tokens_for_project(self.project_id, self.session)
                if tokens is None:
                    raise GatewayUnavailableError(say("forgeCredentialsMissing"))
                await self._release()
                token, _ = await tokens.installation_token()
                owner, repo = binding.repo.split("/", 1)
                client = await status_client(self.project_id, self.session)
                await self._release()
                relation = await client.compare_status(
                    owner=owner, repo=repo, base=ref, head=head, token=token
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

    async def _blob(self, entry):
        data = await self._data(
            self.project_id, self.session, f"/git/blobs/{entry['sha']}"
        )
        if not data or data.get("encoding") != "base64":
            raise GatewayUnavailableError(say("forgeNoFileContent"))
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
                    raise ConflictError(say("fileChangedWhileReading"))
                chunks.append(chunk)
                offset += len(chunk)
            return b"".join(chunks), source
        head = await self.revision()
        return await self._blob(await self._entry(path, head)), source

    async def raw_at(self, path: str, revision: str) -> bytes | None:
        """The file as one commit has it; None when that commit has no such file."""
        try:
            entry = await self._entry(clean_path(path), revision)
        except NotFoundError:
            return None
        return await self._blob(entry)

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
        if task is None or task.status != TaskStatus.open:
            raise ValidationError(say("taskFinishedFilesReadOnly"))
        return await self.live(
            "write_bytes",
            path=clean_path(path),
            data=base64.b64encode(data).decode(),
            version=version,
        )
