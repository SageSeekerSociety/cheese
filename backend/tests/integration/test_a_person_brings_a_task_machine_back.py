"""A person who edits a task's files gets the task's machine back, without
waiting for its AI teammate to start a turn.

The task's sandbox goes when it sits idle: destroyed, or parked with its home
left on the host. Its files still open (read off the forge). Saving one, or
asking for the machine ahead of the first save, brings the task's own machine
back — its sandbox, the task's checkout, the work it kept laid back — and the
teammate's next tool call lands in that same machine and is still told that its
sandbox changed.

The hosts are the ones of ``test_sandbox_idle_stop``. Their executors answer
here: an install starts one, a destroyed home takes it with it, and a running
one answers its file calls on the host's directory.
"""

import asyncio
import json
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.device_failures import EXECUTOR_UNREACHABLE, DeviceCallError
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.harness.claude_code.remote_execution import launch, runtime
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.services import IdentityService
from app.domain.machine import task_machine
from app.domain.machine.models import SandboxHome
from app.domain.machine.sandbox_wait import SANDBOX_LOST_KEPT
from app.domain.topic.models import Topic
from tests.delivery import delivery_task
from tests.integration.test_a_checkpoint_after_its_sandbox_is_gone import (
    _a_cloud_machine,
)
from tests.integration.test_file_panel_safety import _owner
from tests.integration.test_sandbox_freezing import (
    freeze_sweep,
    frozen_for,
    home_row,
)
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    run,
    sweep,
    time_passes,
    tool_call,
    working_on,
)
from tests.machine_work import machine_commits, machine_keeps
from tests.support import git_store
from tests.support.files_bucket import files_bucket


class Machines:
    """The executors on the hosts: one runs on a host from its install until
    its home is destroyed or parked there, or it stops for sitting idle."""

    def __init__(self, case) -> None:
        self.case = case
        self.running: set[str] = set()
        self.installed: list[str] = []
        self.checkouts: list[str] = []
        self.stopped: set[str] = set()

    def home(self, target) -> Path:
        return (
            self.case.hosts.home_dir(target["device_id"])
            / ".cheese"
            / "home"
            / str(self.case.project_id)
            / target["resource_id"]
        )

    async def exec(self, device_id, argv, *, stdin=None, timeout=60, **kwargs):
        if argv[:1] == ["sh"]:
            # The project environment's state: set up, as one with nothing to
            # install is.
            return {"exit": 0, "stdout": json.dumps({"state": "ready"}), "stderr": ""}
        answer = await self._exec(
            device_id, argv, stdin=stdin, timeout=timeout, **kwargs
        )
        if stdin and "def main(cleanup, request)" in stdin:
            action = self.case.hosts.actions[-1][1]
            if action in ("destroy", "park"):
                self.running.discard(device_id)
        else:
            self.installed.append(device_id)
            self.running.add(device_id)
            self.stopped.discard(device_id)
        return answer

    async def executor(self, target, method, params, **kwargs):
        device_id = target["device_id"]
        if method == "ping" and device_id in self.running - self.stopped:
            # A running executor of this release, in the sandbox it was
            # installed in: what a call there prepares in place.
            async with self.case.client.test_request_factory() as db:
                image = await db.scalar(
                    select(SandboxHome.sandbox_image).where(
                        SandboxHome.resource_id == target["resource_id"]
                    )
                )
            return {
                **await self._executor(target, method, params, **kwargs),
                "capabilities": ["prepare"],
                "protocol_version": runtime.PROTOCOL_VERSION,
                "files": launch._file_digests(),
                "sandbox": True,
                "sandbox_image": image,
            }
        if method in ("ping", "task_fs", "invoke") and (
            device_id not in self.running or device_id in self.stopped
        ):
            raise DeviceCallError(
                "dial unix /tmp/cheese-execution-0.sock: connect: no such file",
                failure_code=EXECUTOR_UNREACHABLE,
            )
        if method == "prepare":
            return {
                "state": f"/{device_id}/state",
                "workspace": f"/{device_id}/work",
                "mcp_servers": [],
            }
        if method == "invoke" and "cheese worktree" in params["args"]["command"]:
            path = await asyncio.to_thread(self._checkout, target)
            self.checkouts.append(device_id)
            return {"value": {"stdout": f"{path}\n", "stderr": ""}}
        if method == "task_fs":
            executor = object.__new__(Executor)
            executor.env = {"HOME": str(self.home(target))}
            return executor.task_fs(params)
        return await self._executor(target, method, params, **kwargs)

    def _checkout(self, target) -> Path:
        """`cheese worktree`: the task's branch, with what its last sandbox
        kept laid back over it when the branch is where it was kept."""
        task_id = self.case.task.id
        repo = git_store.path(self.case.project_id)
        task_dir = self.home(target) / ".cheese" / "tasks" / str(task_id)
        if (task_dir / ".git").exists():
            return task_dir
        branch = git_store.branch_for_task(task_id)
        git = ["git", "-C", str(task_dir)]
        task_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "-q", "-b", branch, str(repo), str(task_dir)], check=True
        )
        kept = f"refs/cheese/kept/{task_id}"
        parent = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "-q", "--verify", f"{kept}^"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        head = git_store.git(repo, "rev-parse", branch).strip()
        if parent and parent == head:
            subprocess.run([*git, "fetch", "-q", str(repo), kept], check=True)
            subprocess.run(
                [*git, "checkout", "-q", "FETCH_HEAD", "--", "."], check=True
            )
            subprocess.run([*git, "reset", "-q"], check=True)
        return task_dir

    def stop_executor(self, device_id: str) -> None:
        """The executor stops for sitting idle; its home stays."""
        self.stopped.add(device_id)


@pytest.fixture
def bucket(monkeypatch):
    """The files bucket a Cloud sandbox's root is saved to when it goes."""
    with files_bucket() as config:
        for name, value in (
            ("s3_endpoint_url", config["files_bucket_endpoint_url"]),
            ("s3_access_key", config["files_bucket_access_key"]),
            ("s3_secret_key", config["files_bucket_secret_key"]),
            ("s3_region", config["files_bucket_region"]),
            ("transcript_s3_bucket", config["files_bucket"]),
        ):
            monkeypatch.setattr(settings, name, value)
        yield


@pytest.fixture
def task_cloud(cloud, bucket, monkeypatch):
    """The first room's task, worked by its own session on a Cloud host."""
    machines = Machines(cloud)
    machines._exec = cloud.hosts.exec
    machines._executor = cloud.hosts.executor
    monkeypatch.setattr(cloud.hosts, "exec", machines.exec)
    monkeypatch.setattr(execution, "call", machines.executor)
    monkeypatch.setattr(task_machine, "ROUND_S", 0.3)
    monkeypatch.setattr(task_machine, "SAVE_WAIT_S", 10.0)
    room = cloud.seats[0].room
    task = delivery_task(cloud.client, room)

    async def seed():
        async with cloud.client.test_request_factory() as db:
            topic = await db.get(Topic, room)
            agent = await IdentityService(db).ensure_room_agent_user(room)
            row = await AgentSessionService(db).ensure(
                task.id, "cheese", harness="claude-code"
            )
            resource = str(topic.resource_id or room)
            row.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
            }
            token = bind_resource_token(
                mint_scoped_token(
                    project_id=str(cloud.project_id),
                    topic_id=str(task.id),
                    agent_handle=agent.username,
                ),
                resource,
                session_id=str(row.id),
            )
            await db.commit()
            return SimpleNamespace(room=task.id, session=row.id, token=token)

    cloud.task = task
    cloud.seat = run(cloud, seed)
    cloud.machines = machines
    cloud.headers = _owner(cloud.client)
    machine_commits(cloud.project_id, task.id, {"notes.md": "committed\n"})
    _a_cloud_machine(cloud, "host-a")
    working_on(cloud, cloud.seat, "host-a")
    run_checkout(cloud, "host-a")
    return cloud


def run_checkout(case, device_id) -> None:
    """The teammate's first command opened the task's checkout there."""
    target = {
        "device_id": device_id,
        "resource_id": home_row(case, case.seat).resource_id,
    }
    case.machines._checkout(target)


def read(case, path):
    return case.client.get(
        f"/projects/{case.project_id}/file",
        params={"source": "live", "path": path, "task": str(case.task.id)},
        headers=case.headers,
    )


def save(case, path, content, *, version, base):
    return case.client.put(
        f"/projects/{case.project_id}/file",
        params={"task": str(case.task.id)},
        json={"path": path, "content": content, "version": version, "base": base},
        headers=case.headers,
    )


def prepare(case):
    return case.client.post(
        f"/topics/{case.task.id}/machine/prepare", headers=case.headers
    )


def status(case) -> dict:
    answer = case.client.get(
        f"/topics/{case.task.id}/machine/status", headers=case.headers
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["data"]


def until_settled(case, within_s: float = 15.0) -> dict:
    """What the status says once nothing is being prepared any more."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        now = status(case)
        if now["machine"] != "preparing":
            return now
        time.sleep(0.1)
    raise AssertionError("the task's machine was still being prepared")


def turns(case) -> int:
    async def count():
        async with case.client.test_request_factory() as db:
            return len(
                list(
                    await db.scalars(
                        select(AgentTurn.id).where(
                            AgentTurn.conversation_id == case.task.id
                        )
                    )
                )
            )

    return run(case, count)


def destroyed_with_work_kept(case) -> None:
    """A person's edit was kept off the sandbox, then it sat idle and was
    destroyed with its home."""
    machine_keeps(case.project_id, case.task.id, {"notes.md": "kept edit\n"})
    time_passes(case, case.seat, timedelta(minutes=11))
    assert sweep(case)["destroyed"] == 1
    assert home_row(case, case.seat) is None


def test_a_save_brings_a_destroyed_sandbox_back_with_its_kept_work(task_cloud):
    case = task_cloud
    destroyed_with_work_kept(case)
    opened = read(case, "notes.md").json()["data"]
    assert opened["content"] == "kept edit\n"

    saved = save(
        case,
        "notes.md",
        "edited by a person\n",
        version=opened["version"],
        base=opened["content"],
    )

    assert saved.status_code == 200, saved.text
    assert status(case) == {"machine": "ready", "failed": None}
    again = read(case, "notes.md").json()["data"]
    assert again["content"] == "edited by a person\n"
    assert again["editable"] is True
    assert turns(case) == 0


def test_the_teammate_lands_in_the_machine_a_person_brought_back_and_is_told(
    task_cloud,
):
    case = task_cloud
    destroyed_with_work_kept(case)
    assert prepare(case).json()["data"]["started"] is True
    assert until_settled(case) == {"machine": "ready", "failed": None}
    installed = len(case.machines.installed)

    told = tool_call(case, case.seat, tells_agent=True)
    again = tool_call(case, case.seat, tells_agent=True)

    assert told["target"]["device_id"] == "host-a"
    assert told["notice"] == SANDBOX_LOST_KEPT
    assert "notice" not in again
    # Prepared where it runs, not installed again for the turn.
    assert len(case.machines.installed) == installed
    assert read(case, "notes.md").json()["data"]["content"] == "kept edit\n"


def test_a_person_and_a_turn_at_once_get_one_machine(task_cloud, monkeypatch):
    case = task_cloud
    destroyed_with_work_kept(case)
    installing = case.machines.exec

    async def slow_install(device_id, argv, *, stdin=None, **kwargs):
        if not (stdin and "def main(cleanup, request)" in stdin):
            await asyncio.sleep(0.5)
        return await installing(device_id, argv, stdin=stdin, **kwargs)

    monkeypatch.setattr(case.hosts, "exec", slow_install)
    before = len(case.machines.installed)

    assert prepare(case).json()["data"]["started"] is True
    told = tool_call(case, case.seat, timeout=10, tells_agent=True)
    settled = until_settled(case)

    assert settled == {"machine": "ready", "failed": None}
    assert told["target"]["device_id"] == "host-a"
    assert len(case.machines.installed) == before + 1


def test_two_asks_for_the_machine_bring_up_one_and_agree_how_far_it_got(
    task_cloud, monkeypatch
):
    """How far a bring-up got is a fact about the task's machine, not about
    the backend process that started it: a second ask, wherever it lands,
    starts nothing and reads the same state."""
    case = task_cloud
    destroyed_with_work_kept(case)
    installing = case.machines.exec

    async def slow_install(device_id, argv, *, stdin=None, **kwargs):
        if not (stdin and "def main(cleanup, request)" in stdin):
            await asyncio.sleep(0.5)
        return await installing(device_id, argv, stdin=stdin, **kwargs)

    monkeypatch.setattr(case.hosts, "exec", slow_install)
    before = len(case.machines.installed)

    first = prepare(case).json()["data"]
    second = prepare(case).json()["data"]

    assert first == {"started": True, "reason": None}
    assert second == {"started": False, "reason": "preparing"}
    assert status(case) == {"machine": "preparing", "failed": None}
    assert until_settled(case) == {"machine": "ready", "failed": None}
    assert len(case.machines.installed) == before + 1


def test_a_save_starts_an_executor_that_stopped_for_sitting_idle(task_cloud):
    case = task_cloud
    opened = read(case, "notes.md").json()["data"]
    case.machines.stop_executor("host-a")

    saved = save(
        case,
        "notes.md",
        "saved after a quiet hour\n",
        version=opened["version"],
        base=opened["content"],
    )

    assert saved.status_code == 200, saved.text
    assert read(case, "notes.md").json()["data"]["content"] == (
        "saved after a quiet hour\n"
    )


def test_a_sandbox_a_person_is_working_in_is_not_taken_as_idle(task_cloud):
    case = task_cloud
    time_passes(case, case.seat, timedelta(minutes=11))

    assert read(case, "notes.md").status_code == 200
    swept = sweep(case)

    assert swept["destroyed"] == 0
    assert home_row(case, case.seat) is not None


def test_reading_does_not_bring_a_machine_back(task_cloud):
    case = task_cloud
    destroyed_with_work_kept(case)
    installed = len(case.machines.installed)

    opened = read(case, "notes.md")

    assert opened.status_code == 200, opened.text
    assert opened.json()["data"]["editable"] is False
    assert status(case) == {"machine": None, "failed": None}
    assert len(case.machines.installed) == installed


def test_a_save_while_the_machine_is_still_coming_up_says_to_try_again(
    task_cloud, monkeypatch
):
    case = task_cloud
    destroyed_with_work_kept(case)
    opened = read(case, "notes.md").json()["data"]
    monkeypatch.setattr(task_machine, "SAVE_WAIT_S", 0.0)
    installing = case.machines.exec

    async def slow_install(device_id, argv, *, stdin=None, **kwargs):
        if not (stdin and "def main(cleanup, request)" in stdin):
            await asyncio.sleep(1.0)
        return await installing(device_id, argv, stdin=stdin, **kwargs)

    monkeypatch.setattr(case.hosts, "exec", slow_install)

    early = save(case, "notes.md", "x\n", version=opened["version"], base="kept edit\n")

    assert early.status_code == 503, early.text
    assert early.json()["error"]["name"] == "MachinePreparing"
    assert early.json()["error"]["retryable"] is True
    assert until_settled(case)["machine"] == "ready"
    later = save(case, "notes.md", "x\n", version=opened["version"], base="kept edit\n")
    assert later.status_code == 200, later.text


def test_a_task_nobody_worked_on_has_its_machine_brought_up(cloud):
    """A person need not wait for an agent to have worked in a task: asking
    for its machine starts bringing one up."""
    task = delivery_task(cloud.client, cloud.seats[1].room)

    answer = cloud.client.post(
        f"/topics/{task.id}/machine/prepare", headers=_owner(cloud.client)
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["started"] is True


@pytest.fixture
def parked_task(task_cloud, monkeypatch):
    """The task's sandbox frozen once idle, and parked past its keep time."""
    case = task_cloud
    monkeypatch.setattr(settings, "cloud_sandbox_freeze", True)
    time_passes(case, case.seat, timedelta(minutes=20))
    freeze_sweep(case)
    frozen_for(
        case, case.seat, timedelta(seconds=settings.cloud_sandbox_frozen_keep_s + 60)
    )
    freeze_sweep(case)
    assert home_row(case, case.seat).parked_at is not None
    return case


def test_a_save_starts_a_parked_sandbox_again_where_its_home_is(parked_task):
    case = parked_task
    before = home_row(case, case.seat)
    opened = read(case, "notes.md").json()["data"]
    assert opened["editable"] is False

    saved = save(
        case,
        "notes.md",
        "edited while parked\n",
        version=opened["version"],
        base=opened["content"],
    )

    assert saved.status_code == 200, saved.text
    after = home_row(case, case.seat)
    assert (after.host_id, after.resource_id) == (before.host_id, before.resource_id)
    assert after.parked_at is None
    told = tool_call(case, case.seat, tells_agent=True)
    assert "原来的位置重新启动" in told["notice"]


def test_the_machine_s_words_never_reach_the_person(task_cloud, monkeypatch):
    case = task_cloud
    opened = read(case, "notes.md").json()["data"]

    async def refused(target, method, params, **kwargs):
        raise DeviceCallError("lstat /home/cheese/.cheese/executor: no such file")

    monkeypatch.setattr(execution, "call", refused)

    saved = save(
        case, "notes.md", "x\n", version=opened["version"], base=opened["content"]
    )

    assert saved.status_code == 503, saved.text
    message = saved.json()["error"]["message"]
    assert "lstat" not in message and "/home" not in message


@pytest.fixture
def unworked_task(cloud, bucket, monkeypatch):
    """A task of the second room that no agent has worked in: no session of
    its own, nothing placed for it."""
    machines = Machines(cloud)
    machines._exec = cloud.hosts.exec
    machines._executor = cloud.hosts.executor
    monkeypatch.setattr(cloud.hosts, "exec", machines.exec)
    monkeypatch.setattr(execution, "call", machines.executor)
    monkeypatch.setattr(task_machine, "ROUND_S", 0.3)
    monkeypatch.setattr(task_machine, "SAVE_WAIT_S", 10.0)
    cloud.task = delivery_task(cloud.client, cloud.seats[1].room)
    cloud.machines = machines
    cloud.headers = _owner(cloud.client)
    _a_cloud_machine(cloud, "host-b")
    return cloud


def task_host_comes_up(case, device_id) -> None:
    """The pool host placed for the task's environment dials in."""

    async def enrol():
        from app.domain.environment import services as environment_rows
        from app.domain.machine import occupants
        from app.domain.machine.repositories import CloudHostRepository
        from tests.integration.test_sandbox_idle_stop import (
            AiStatus,
            MachineStatus,
            sandbox_image,
        )

        async with case.client.test_request_factory() as db:
            environment = await environment_rows.environment_of_conversation(
                db, case.task.id
            )
            home = await occupants.home_of_environment(db, environment.id)
            if home is None:
                return False
            repo = CloudHostRepository(db)
            host = await repo.get(home.host_id)
            case.provider.machines[host.machine_id].update(
                status="running", aiStatus="disabled"
            )
            host.status = MachineStatus.running
            host.ai_status = AiStatus.disabled
            host.sandbox_image = sandbox_image.default_image()["reference"]
            from datetime import UTC, datetime

            host.last_seen_at = datetime.now(UTC)
            await repo.mark_enrolled(
                host,
                device_id=device_id,
                when=datetime.now(UTC),
                sandbox_disk_gb=host.sandbox_disk_gb,
            )
            await db.commit()
            return True

    # The bring-up places a sandbox for it first, in the background.
    end = time.monotonic() + 15
    while not run(case, enrol):
        assert time.monotonic() < end, "no sandbox was placed for the task"
        time.sleep(0.1)
    case.hosts.online.add(device_id)


def arrange(case, operation, path, to=None):
    return case.client.post(
        f"/projects/{case.project_id}/file/arrange",
        params={"task": str(case.task.id)},
        json={"operation": operation, "path": path, "to": to},
        headers=case.headers,
    )


def test_a_person_edits_the_files_of_a_task_no_agent_worked_in(unworked_task):
    case = unworked_task
    assert prepare(case).json()["data"]["started"] is True
    task_host_comes_up(case, "host-b")
    assert until_settled(case) == {"machine": "ready", "failed": None}
    opened = read(case, f"deliveries/{case.task.id}.txt").json()["data"]

    saved = save(
        case,
        f"deliveries/{case.task.id}.txt",
        "edited by a person\n",
        version=opened["version"],
        base=opened["content"],
    )
    created = arrange(case, "create", "notes/plan.md")
    moved = arrange(case, "move", "notes/plan.md", "notes/done.md")
    deleted = arrange(case, "delete", f"deliveries/{case.task.id}.txt")

    for answer in (saved, created, moved, deleted):
        assert answer.status_code == 200, answer.text
    listed = case.client.get(
        f"/projects/{case.project_id}/files",
        params={"task": str(case.task.id), "source": "live"},
        headers=case.headers,
    ).json()["data"]
    paths = {entry["path"] for entry in listed["data"]}
    assert "notes/done.md" in paths
    assert f"deliveries/{case.task.id}.txt" not in paths
    assert listed["source"] == "live"
    assert turns(case) == 0


def test_someone_not_on_the_project_cannot_bring_the_machine_up(unworked_task):
    case = unworked_task
    from tests.integration.conftest import session_auth_headers

    answer = case.client.post(
        f"/topics/{case.task.id}/machine/prepare",
        headers=session_auth_headers("mallory"),
    )

    assert answer.status_code in (403, 404), answer.text
    assert status(case) == {"machine": None, "failed": None}


def test_an_agent_that_comes_later_works_where_the_person_left_their_file(
    unworked_task,
):
    case = unworked_task
    assert prepare(case).json()["data"]["started"] is True
    task_host_comes_up(case, "host-b")
    assert until_settled(case) == {"machine": "ready", "failed": None}
    assert arrange(case, "create", "from-a-person.md").status_code == 200

    async def seat():
        async with case.client.test_request_factory() as db:
            topic = await db.get(Topic, case.task.room_id)
            agent = await IdentityService(db).ensure_room_agent_user(topic.id)
            row = await AgentSessionService(db).ensure(
                case.task.id, "cheese", harness="claude-code"
            )
            resource = str(topic.resource_id or topic.id)
            row.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
            }
            token = bind_resource_token(
                mint_scoped_token(
                    project_id=str(case.project_id),
                    topic_id=str(case.task.id),
                    agent_handle=agent.username,
                ),
                resource,
                session_id=str(row.id),
            )
            await db.commit()
            return SimpleNamespace(room=case.task.id, session=row.id, token=token)

    agent = run(case, seat)
    hands = tool_call(case, agent)

    assert hands["target"]["device_id"] == "host-b"
    task_dir = (
        case.machines.home(hands["target"]) / ".cheese" / "tasks" / str(case.task.id)
    )
    assert (task_dir / "from-a-person.md").exists()


def test_a_bring_up_whose_process_is_gone_soon_stops_saying_preparing(
    task_cloud, monkeypatch
):
    """The backend process running a bring-up can die with it; the task does
    not go on saying its machine is being prepared, and it can be asked for
    again."""
    case = task_cloud
    destroyed_with_work_kept(case)
    monkeypatch.setattr(task_machine, "HOLD_S", 0.5)

    async def died(*_args, **_kwargs):
        return None

    monkeypatch.setattr(task_machine, "_bring_up", died)
    assert prepare(case).json()["data"]["started"] is True
    assert status(case)["machine"] == "preparing"

    time.sleep(0.7)

    assert status(case) == {"machine": None, "failed": None}
    assert prepare(case).json()["data"]["started"] is True


def test_a_sandbox_a_person_saved_into_is_kept_and_one_open_in_a_tab_is_not(
    task_cloud,
):
    case = task_cloud
    opened = read(case, "notes.md").json()["data"]
    time_passes(case, case.seat, timedelta(minutes=11))

    saved = save(
        case,
        "notes.md",
        "saved within the idle time\n",
        version=opened["version"],
        base=opened["content"],
    )
    assert saved.status_code == 200, saved.text
    assert sweep(case)["destroyed"] == 0
    assert home_row(case, case.seat) is not None

    # Open in a tab, nothing asked of it: the panel does not poll.
    time_passes(case, case.seat, timedelta(minutes=11))
    assert sweep(case)["destroyed"] == 1
    assert home_row(case, case.seat) is None


def test_a_persons_read_that_reaches_the_machine_is_an_act_of_theirs(
    unworked_task,
):
    case = unworked_task
    assert prepare(case).json()["data"]["started"] is True
    task_host_comes_up(case, "host-b")
    assert until_settled(case) == {"machine": "ready", "failed": None}

    async def acted(*, back: timedelta | None = None):
        from sqlalchemy import update

        from app.domain.environment.models import EnvironmentAttachment

        async with case.client.test_request_factory() as db:
            if back is not None:
                await db.execute(
                    update(EnvironmentAttachment)
                    .where(EnvironmentAttachment.user_id.is_not(None))
                    .values(acted_at=EnvironmentAttachment.acted_at - back)
                )
                await db.commit()
            return await db.scalar(
                select(EnvironmentAttachment.acted_at).where(
                    EnvironmentAttachment.user_id.is_not(None)
                )
            )

    before = run(case, lambda: acted(back=timedelta(minutes=5)))
    assert read(case, f"deliveries/{case.task.id}.txt").status_code == 200

    assert run(case, acted) > before + timedelta(minutes=4)
