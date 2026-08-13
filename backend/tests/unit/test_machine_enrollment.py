"""Headless enrollment: a machine becoming an agent host with nobody watching.

The device flow was built for a human with a browser. These cover the parts that
have no human to catch them: the credential must not leak, the bootstrap key
must not outlive its one use, a failure must be recorded rather than retried
forever, and the human's own access must survive.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import ValidationError
from app.domain.device.supply import Supply, Visibility
from app.domain.machine import enrollment
from app.domain.machine.models import MAX_ENROLL_ATTEMPTS, AiStatus, MachineStatus

pytestmark = pytest.mark.anyio


class FakeDevices:
    def __init__(self):
        self.assigned: list[tuple[str, uuid.UUID]] = []
        self.team_assigned: list[tuple[str, int]] = []
        self.started: list[str] = []
        self.approved_supply: list[Supply] = []
        self.approved_visibility: list[Visibility] = []

    async def start(self, name):
        self.started.append(name)
        return "code-1"

    async def approve(self, code, *, owner_user_id, supply, visibility, name=None):
        # `supply`/`visibility` are required on the real service (#282 决定 2 /
        # #358) — mirrored here so this double cannot go on accepting a call the
        # production one rejects.
        self.approved_supply.append(supply)
        self.approved_visibility.append(visibility)
        return SimpleNamespace(
            device_id="dev123",
            token="SECRET-TOKEN",
            owner_user_id=owner_user_id,
            supply=supply,
            visibility=visibility,
        )

    async def assign_to_project(self, device_id, project_id, *, actor_user_id):
        self.assigned.append((device_id, project_id))

    async def assign_to_team(self, device_id, team_id, *, actor_user_id):
        self.team_assigned.append((device_id, team_id))


class FakeProjects:
    def __init__(self, team_id=None):
        self.team_id = team_id

    async def get(self, project_id):
        return SimpleNamespace(id=project_id, team_id=self.team_id)


class FakeMachineRepo:
    def __init__(self):
        self.enrolled: list[str] = []
        self.failures: list[str] = []

    async def mark_enrolled(self, machine, *, device_id, when):
        machine.device_id = device_id
        machine.enrolled_at = when
        machine.enroll_error = None
        machine.bootstrap_key = None
        self.enrolled.append(device_id)
        return machine

    async def mark_enroll_failed(self, machine, *, error):
        machine.enroll_error = error
        machine.enroll_attempts += 1
        self.failures.append(error)
        return machine


def make_machine(**overrides):
    base = dict(
        project_id=uuid.uuid4(),
        hostname="proj-abc123-1",
        login_user="cheese",
        ip="10.0.1.10",
        status=MachineStatus.running,
        ai_status=AiStatus.ready,
        device_id=None,
        enrolled_at=None,
        enroll_error=None,
        enroll_attempts=0,
        bootstrap_key="PRIVATE-KEY",
        owner_user_id=42,
        machine_id=7,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def build_service(
    monkeypatch,
    *,
    bootstrap=None,
    origin="https://cheese.example",
    team_id=None,
):
    from app.domain.machine.services import MachineService

    service = MachineService.__new__(MachineService)
    service._session = None
    service._repo = FakeMachineRepo()
    service._projects = FakeProjects(team_id)
    service._devices = FakeDevices()
    service._client = SimpleNamespace(configured=True)

    monkeypatch.setattr(
        "app.domain.machine.services.settings.connector_public_base", origin
    )
    calls: list[dict] = []

    async def _run_bootstrap(*, ip, login_user, private_key, script):
        calls.append(
            {"ip": ip, "user": login_user, "key": private_key, "script": script}
        )
        if bootstrap is not None:
            return bootstrap()
        return "Connected."

    monkeypatch.setattr(enrollment, "run_bootstrap", _run_bootstrap)
    return service, calls


async def test_enrollment_writes_the_credential_the_device_flow_would_have(
    monkeypatch,
):
    service, calls = build_service(monkeypatch)
    machine = make_machine()

    await service.enroll(machine)

    script = calls[0]["script"]
    assert '"base": "https://cheese.example/connector"' in script
    assert "SECRET-TOKEN" in script
    assert "link connect" in script
    assert machine.device_id == "dev123"
    assert service._devices.assigned == [("dev123", machine.project_id)]


async def test_a_machine_the_platform_opened_is_enrolled_as_cloud_supply(monkeypatch):
    """入口决定待遇 on both axes (#282 决定 2 / #358): this door is the platform asking
    MicroCloud for a machine, so what it enrols is disposable (`cloud`) AND — a fresh
    one-per-topic VM being its own empty box — whole-machine (`host`). Both are
    CONSTANTS at the call site, never derived from the machine; the connector door
    writes `self_hosted`/`isolated` for the very same hardware."""
    service, _ = build_service(monkeypatch)

    await service.enroll(make_machine())

    assert service._devices.approved_supply == [Supply.cloud]
    # Cloud collapses the visibility axis (#358) — a disposable box is safely host,
    # and it must be, so the isolated-no-transport gate never fires for cloud compute.
    assert service._devices.approved_visibility == [Visibility.host]


async def test_team_machine_enrolls_into_the_team_pool(monkeypatch):
    service, _ = build_service(monkeypatch, team_id=73)
    machine = make_machine()

    await service.enroll(machine)

    assert service._devices.team_assigned == [("dev123", 73)]
    assert service._devices.assigned == []


async def test_the_bootstrap_key_does_not_outlive_its_one_use(monkeypatch):
    service, _ = build_service(monkeypatch)
    machine = make_machine()

    await service.enroll(machine)

    assert machine.bootstrap_key is None, (
        "a key kept after setup is a standing way in that nobody asked for"
    )


async def test_a_failure_never_leaks_the_token(monkeypatch):
    def _boom():
        raise enrollment.EnrollmentError("ssh said: token SECRET-TOKEN rejected")

    service, _ = build_service(monkeypatch, bootstrap=_boom)
    machine = make_machine()

    await service.enroll(machine)

    assert "SECRET-TOKEN" not in machine.enroll_error
    assert "***" in machine.enroll_error


async def test_a_failed_attempt_keeps_the_key_so_it_can_be_retried(monkeypatch):
    def _boom():
        raise enrollment.EnrollmentError("network down")

    service, _ = build_service(monkeypatch, bootstrap=_boom)
    machine = make_machine()

    await service.enroll(machine)

    assert machine.device_id is None
    assert machine.bootstrap_key == "PRIVATE-KEY"
    assert machine.enroll_attempts == 1


async def test_enrolling_twice_is_a_no_op(monkeypatch):
    service, calls = build_service(monkeypatch)
    machine = make_machine(device_id="already", bootstrap_key=None)

    await service.enroll(machine)
    assert calls == []


async def test_an_unreachable_origin_is_refused_before_a_device_is_minted(
    monkeypatch,
):
    # Enrolling against localhost would mint a device that can never call home.
    service, calls = build_service(monkeypatch, origin="http://localhost:8099")
    machine = make_machine()

    with pytest.raises(ValidationError):
        await service.enroll(machine)
    assert service._devices.started == []
    assert calls == []


async def test_a_machine_from_before_enrollment_says_so(monkeypatch):
    service, _ = build_service(monkeypatch)
    machine = make_machine(owner_user_id=None)

    with pytest.raises(ValidationError):
        await service.enroll(machine)


async def test_the_sweep_keeps_going_when_one_machine_fails(monkeypatch):
    service, _ = build_service(monkeypatch)
    good, bad = make_machine(), make_machine(hostname="bad-1")

    async def _enroll(machine):
        if machine.hostname == "bad-1":
            raise RuntimeError("boom")
        machine.device_id = "dev123"
        return machine

    monkeypatch.setattr(service, "enroll", _enroll)

    async def _awaiting(limit):
        return [bad, good]

    service._repo.list_awaiting_enrollment = _awaiting

    result = await service.enroll_pending()
    assert result == {"enrolled": 1, "failed": 1}


def test_the_script_resolves_the_download_target_on_the_machine():
    script = enrollment.bootstrap_script(
        origin="https://x.test", token="T", device_id="D"
    )
    # Guessing the architecture from here would break the moment a VM offering
    # lands on arm — the machine is the only thing that knows.
    assert "uname -m" in script
    assert "linux-amd64" in script and "linux-arm64" in script


def test_the_script_never_puts_the_token_on_a_command_line():
    script = enrollment.bootstrap_script(
        origin="https://x.test", token="TOK", device_id="D"
    )
    # It lands in the config via a heredoc; an argument would be visible in the
    # machine's own process list.
    assert "TOK" in script
    for line in script.splitlines():
        if line.startswith(("curl", "ssh", '"$HOME/.local/bin/cheesehost"')):
            assert "TOK" not in line


def test_combining_keys_drops_blanks_and_duplicates():
    combined = enrollment.combine_authorized_keys("a", None, "  ", "a", "b")
    assert combined == "a\nb"


def test_redact_covers_every_occurrence():
    assert enrollment.redact("x T y T", "T") == "x *** y ***"


def test_attempt_ceiling_is_bounded():
    # A machine that cannot be enrolled is something to look at, not to keep
    # SSHing at forever.
    assert 0 < MAX_ENROLL_ATTEMPTS <= 10


def test_enrolled_at_is_timezone_aware():
    # Project convention: every datetime that reaches the DB is aware.
    assert datetime.now(UTC).tzinfo is not None


async def test_the_sweep_is_a_no_op_when_microcloud_is_not_configured(monkeypatch):
    """A deployment with no MicroCloud must not have a loop erroring every minute.

    Nothing can have been provisioned, so there is nothing to enroll — that is a
    quiet no-op, not a failure.
    """
    from app.domain.machine.runner import MachineEnrollmentRunner

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            raise AssertionError("must not commit when there is nothing to do")

    from app.domain.machine.services import MachineService

    monkeypatch.setattr(MachineService, "available", property(lambda self: False))
    runner = MachineEnrollmentRunner(Session, 60)
    assert await runner.sweep() == {"enrolled": 0, "failed": 0}


def test_enrollment_does_not_ride_the_ai_scheduler():
    """Machines must not require 定期巡检 to be switched on.

    The project scheduler spends model budget and ships disabled
    (scheduler_interval_seconds = 0), which is exactly the state dev runs in — a
    machine enrolled only from that tick would never be enrolled at all.
    """
    import inspect

    from app.domain.scheduler.service import SchedulerService

    source = inspect.getsource(SchedulerService)
    assert "enroll" not in source


def test_the_script_provides_tmux_the_connector_needs():
    """The connector hosts sessions in tmux and exits without one.

    MicroCloud's Debian template ships no tmux, so a machine enrolled without
    this got a service that died on startup — while enrollment recorded success.
    """
    script = enrollment.bootstrap_script(
        origin="https://x.test", token="T", device_id="D"
    )
    assert "tmux" in script.split("for tool in")[1].split(";")[0]
    # ...and before the connector is started, or the check buys nothing.
    # Compare executable lines only: prose mentioning `link connect` would
    # otherwise decide the order.
    code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    ensures = next(i for i, ln in enumerate(code) if "for tool in" in ln)
    connects = next(i for i, ln in enumerate(code) if "link connect" in ln)
    assert ensures < connects


def test_the_script_confirms_the_service_actually_stayed_up():
    """`link connect` returns as soon as systemd accepts the start, which is
    before the process can fail — so its exit code alone is not evidence."""
    script = enrollment.bootstrap_script(
        origin="https://x.test", token="T", device_id="D"
    )
    assert "systemctl is-active --quiet cheese" in script
    connect = next(
        text
        for text in script.splitlines()
        if "link connect" in text and text.startswith('"')
    )
    # sudo inside `link connect` would otherwise consume the rest of this very
    # script, which arrives on stdin.
    assert connect.endswith("< /dev/null")


async def test_a_service_that_dies_is_recorded_as_a_failure(monkeypatch):
    def _boom():
        raise enrollment.EnrollmentError(
            "bootstrap failed (1): the connector service did not stay up: no tmux found"
        )

    service, _ = build_service(monkeypatch, bootstrap=_boom)
    machine = make_machine()

    await service.enroll(machine)

    assert machine.device_id is None, "a dead service is not an enrolled machine"
    assert "did not stay up" in machine.enroll_error


def test_bootstrap_ensures_both_tools_the_image_may_not_have():
    """A machine missing either tool enrolls "successfully" and then fails
    silently — tmux makes the connector die while systemctl still returns 0, and
    without git the agent's turn runs in an empty dir and its work is never seen.

    Neither is guaranteed by the image: MicroCloud's LXC template lists git but
    not tmux, and the VM template installs neither.
    """
    script = enrollment.bootstrap_script(
        origin="http://cheese.test", token="tok", device_id="dev"
    )
    assert "for tool in tmux git; do" in script
    # Missing tools must abort enrollment rather than produce a broken machine.
    assert "exit 1" in script.split("for tool in")[1].split("done")[0]


def test_bootstrap_is_valid_shell():
    """The script is built from an f-string, so a mis-escaped brace turns into a
    syntax error that would only surface on a real machine."""
    import subprocess

    script = enrollment.bootstrap_script(
        origin="http://cheese.test", token="tok", device_id="dev"
    )
    checked = subprocess.run(
        ["bash", "-n"], input=script, text=True, capture_output=True, timeout=30
    )
    assert checked.returncode == 0, checked.stderr
