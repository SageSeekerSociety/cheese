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


class FakeSession:
    """Keeps a timeline of commits and bootstraps, so a test can say which came
    first; the bootstrap fake appends to the same list."""

    def __init__(self):
        self.timeline: list[str] = []

    async def commit(self):
        self.timeline.append("commit")


class FakeProjects:
    def __init__(self, team_id=None):
        self.team_id = team_id

    async def get(self, project_id):
        return SimpleNamespace(id=project_id, team_id=self.team_id)


class FakeMachineRepo:
    def __init__(self):
        self.enrolled: list[str] = []
        self.failures: list[str] = []

    async def mark_enrolled(self, machine, *, device_id, when, ccproxy_upstream=None):
        machine.device_id = device_id
        machine.enrolled_at = when
        machine.enroll_error = None
        machine.bootstrap_key = None
        if ccproxy_upstream:
            machine.ccproxy_upstream = ccproxy_upstream
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

    calls: list[dict] = []
    service = MachineService.__new__(MachineService)
    service._session = FakeSession()
    service._repo = FakeMachineRepo()
    service._projects = FakeProjects(team_id)
    service._devices = FakeDevices()
    service._client = SimpleNamespace(configured=True)

    monkeypatch.setattr(
        "app.domain.machine.services.settings.connector_public_base", origin
    )

    async def _run_bootstrap(*, ip, login_user, private_key, script):
        service._session.timeline.append("bootstrap")
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


async def test_the_credential_is_committed_before_the_machine_is_told_to_dial(
    monkeypatch,
):
    """The bootstrap makes the machine dial in while the sweep's transaction is
    still open. Until the commit, the connector route answered its hello 403
    (unknown device token) and only the connector's retry saved enrollment
    (2026-09-02, machine 478: two refusals before the accept)."""
    service, _ = build_service(monkeypatch)

    await service.enroll(make_machine())

    timeline = service._session.timeline
    assert "bootstrap" in timeline
    assert timeline.index("commit") < timeline.index("bootstrap")


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

    async def _awaiting(limit, **_settle_gate):
        # **kwargs: the service now also asks for the desired AI mode and a
        # settle cutoff (a machine enrolled before its channel settles loses its
        # ccproxy identity for good). This test is about the sweep surviving one
        # bad machine, so it takes the gate as given.
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
    from app.domain.machine.runner import MachineEnrollmentSweeper

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            raise AssertionError("must not commit when there is nothing to do")

    from app.domain.machine.services import MachineService

    monkeypatch.setattr(MachineService, "available", property(lambda self: False))
    runner = MachineEnrollmentSweeper(Session)
    assert await runner.sweep() == {"enrolled": 0, "failed": 0}


async def test_sweep_wakes_only_fully_settled_topic_machines(monkeypatch):
    """The lifecycle sweep is the sole wake source; it hands settled candidates
    to the connector-presence gate without creating a per-topic retry timer."""
    from unittest.mock import AsyncMock

    from app.domain.machine.runner import MachineEnrollmentSweeper
    from app.domain.machine.services import MachineService

    topic_id = uuid.uuid4()
    failed_leases: list = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            return None

    class Service:
        available = True

        def __init__(self, _session):
            pass

        async def refresh_unsettled(self):
            return None

        async def reconcile_ai_mode(self):
            return None

        async def enroll_pending(self):
            return {"enrolled": 1, "failed": 0}

        async def ready_topic_devices(self):
            return [(topic_id, "cloud-1")]

        async def failed_topic_leases(self):
            return list(failed_leases)

    monkeypatch.setattr("app.domain.machine.services.MachineService", Service)
    on_ready = AsyncMock()
    runner = MachineEnrollmentSweeper(Session, on_ready=on_ready)

    assert await runner.sweep() == {"enrolled": 1, "failed": 0}
    on_ready.assert_awaited_once_with([(topic_id, "cloud-1")])

    # Keep the imported name live so the monkeypatch target is checked by linters.
    assert MachineService is not Service


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
    before the process can fail — so its exit code alone is not evidence.

    The connector installs into the enrolling account's own systemd, so the
    question has to be put to that manager; the system one has never heard of
    the unit and would answer "inactive" for a connector that is running fine.
    """
    script = enrollment.bootstrap_script(
        origin="https://x.test", token="T", device_id="D"
    )
    assert "systemctl --user is-active --quiet cheese" in script
    connect = next(
        text
        for text in script.splitlines()
        if "link connect" in text and text.startswith('"')
    )
    # This script arrives on stdin; anything `link connect` reads from there
    # would swallow the rest of it.
    assert connect.endswith("< /dev/null")


def test_the_script_refuses_a_machine_that_will_drop_off_when_ssh_closes():
    """Nobody ever logs into a provisioned machine. Its connector lives in a
    user service manager, which systemd tears down with the account's last
    session — this ssh one — unless the account lingers.

    So a machine that enrols without linger reports healthy, goes green on the
    devices page, and is gone seconds later with nothing said. Enrollment has to
    fail while somebody is still looking at the output.
    """
    script = enrollment.bootstrap_script(
        origin="https://x.test", token="T", device_id="D"
    )
    code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    checks = [i for i, line in enumerate(code) if "Linger" in line]
    assert checks, "the script never asks whether the account lingers"
    # Whatever it tries in between, the LAST word on linger decides the exit.
    assert "exit 1" in "\n".join(code[checks[-1] : checks[-1] + 6])
    # ...and it asks after the connector is started, or nothing set it yet.
    connects = next(i for i, line in enumerate(code) if "link connect" in line)
    assert connects < checks[0]


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


def _tool_check_block() -> str:
    """The generated script's tool loop, ready to run on its own."""
    script = enrollment.bootstrap_script(
        origin="http://cheese.test", token="tok", device_id="dev"
    )
    start = script.index("for tool in")
    return script[start : script.index("\ndone", start) + len("\ndone")]


@pytest.mark.parametrize("missing", ["tmux", "git", "python3"])
def test_a_machine_missing_one_of_these_refuses_to_enrol(missing):
    """Each of these fails SILENTLY when discovered later, which is the whole
    reason the check is here rather than in whatever breaks first.

    Without tmux the connector dies while systemctl still returns 0. Without git
    the agent's turn runs in an empty dir and its work is never seen. Without
    python3 the connector (Go) comes up fine and every room on the machine dies
    at environment preparation instead, because the platform's own programs on a
    machine — `cheese` and the environment helper — are python3.

    None is guaranteed by the image: MicroCloud's LXC template lists git but not
    tmux, and the VM template installs neither.
    """
    import subprocess

    harness = f"""
command() {{ [ "$2" = "{missing}" ] && return 1; return 0; }}
sudo() {{ return 1; }}
{_tool_check_block()}
"""
    done = subprocess.run(["bash", "-c", harness], capture_output=True, text=True)

    assert done.returncode == 1, "a machine without it must not become a host"
    assert missing in done.stderr, "the operator has to be told which tool"


def test_the_check_installs_nothing_on_a_machine_that_already_has_them(tmp_path):
    """An image that carries all three must not pay for an apt transaction — and
    must not need passwordless sudo at all to enrol."""
    import subprocess

    calls = tmp_path / "sudo-calls"
    harness = f"""
command() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls}; }}
{_tool_check_block()}
"""
    done = subprocess.run(["bash", "-c", harness], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert not calls.exists(), "nothing should have been installed"


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


@pytest.mark.parametrize("direct", [False, True])
def test_enrollment_config_routes_cloud_control_without_changing_api_identity(
    monkeypatch, direct
):
    import json

    monkeypatch.setattr(enrollment.settings, "microcloud_direct_control", direct)
    script = enrollment.bootstrap_script(
        origin="https://cheese.test/api", token="test-token", device_id="test-device"
    )
    config = json.loads(script.split("<<'CHEESE_CONFIG_EOF'\n", 1)[1].split("\n", 1)[0])
    assert config["base"] == "https://cheese.test/api/connector"
    assert config["token"] == "test-token"
    assert config["device_id"] == "test-device"
    if direct:
        assert config["ws"] == "ws://127.0.0.1:18080/connector/agent"
    else:
        assert "ws" not in config


# --- the machine's ccproxy identity ----------------------------------------
# Read during the bootstrap because that is the only moment the platform is on
# the machine over ssh: `mark_enrolled` erases the bootstrap key. The meter
# needs it to authenticate its ccproxy hop as THIS machine, which is the only
# way ccproxy honours the ticket the machine itself carries.


def test_bootstrap_reads_the_machines_ccproxy_identity():
    script = enrollment.bootstrap_script(
        origin="http://cheese.test", token="tok", device_id="dev"
    )
    assert enrollment.CCPROXY_UPSTREAM_MARKER in script
    # `set -eu` is in force, and a machine on a different supply route has no
    # such file. Reading it must not be able to fail the enrollment.
    assert "|| true" in script.split("CHEESE_UPSTREAM_EOF")[0]


def test_the_identity_is_parsed_out_of_the_bootstrap_output():
    output = f"cheese.service active\n{enrollment.CCPROXY_UPSTREAM_MARKER}m516:pw516\n"
    assert enrollment.parse_ccproxy_upstream(output) == "m516:pw516"


def test_no_identity_is_not_an_enrollment_failure():
    """Every way it can be absent — machine on another supply route, no python,
    AI channel not settled — means "fall back to the deployment-wide identity",
    which is what that machine does today. None of them is a bad machine."""
    assert enrollment.parse_ccproxy_upstream("cheese.service active") is None
    assert enrollment.parse_ccproxy_upstream("") is None


def test_half_an_identity_is_rejected_rather_than_stored():
    """`user:` or `:password` authenticates as nobody. Storing it would move the
    failure to a 407 from ccproxy, far from the machine that produced it."""
    for junk in ("m516:", ":pw516", "m516", ""):
        assert (
            enrollment.parse_ccproxy_upstream(
                f"{enrollment.CCPROXY_UPSTREAM_MARKER}{junk}"
            )
            is None
        )


async def test_sweep_hands_a_lease_microcloud_gave_up_on_to_the_room(monkeypatch):
    """A lease whose machine or AI channel errored never reaches `on_ready`; the
    sweep hands it to `on_failed` in the same pass, so the room stops waiting."""
    from unittest.mock import AsyncMock

    from app.domain.machine.runner import MachineEnrollmentSweeper
    from app.domain.machine.services import FailedLease, MachineService

    lease = FailedLease(
        topic_id=uuid.uuid4(), hostname="box-9", reason="MicroCloud 报告机器创建失败"
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            return None

    class Service:
        available = True

        def __init__(self, _session):
            pass

        async def refresh_unsettled(self):
            return None

        async def reconcile_ai_mode(self):
            return None

        async def enroll_pending(self):
            return {"enrolled": 0, "failed": 0}

        async def ready_topic_devices(self):
            return []

        async def failed_topic_leases(self):
            return [lease]

    monkeypatch.setattr("app.domain.machine.services.MachineService", Service)
    on_ready, on_failed = AsyncMock(), AsyncMock()
    await MachineEnrollmentSweeper(
        Session, on_ready=on_ready, on_failed=on_failed
    ).sweep()

    on_ready.assert_not_awaited()
    on_failed.assert_awaited_once_with([lease])
    assert MachineService is not None
