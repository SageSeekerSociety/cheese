"""How many test workers a room's tools start, from the CPU it is given.

`pytest -n auto` and vitest size themselves from the processors the machine
reports, and a sandbox's `cpu.max` does not change what they report: `nproc`
still answers the host's cores, so four sandboxes on a four-core host would
start sixteen workers. What is asked here is that the environment an agent's
tools inherit says the quota instead — and says nothing where there is no
quota, so a room's tools keep the default they had.
"""

import sys
import uuid

from app.core.config import settings
from app.domain.agent import worker_quota
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

_NAMES = ("PYTEST_XDIST_AUTO_NUM_WORKERS", "VITEST_MAX_FORKS")


def _cgroup(tmp_path, cpu_max):
    """A cgroup tree whose one group reads `cpu_max`, and a /proc/self/cgroup
    naming that group the way the kernel does."""
    group = tmp_path / "cgroup/cheese-sandboxes/room"
    group.mkdir(parents=True)
    (group / "cpu.max").write_text(cpu_max, encoding="utf-8")
    own = tmp_path / "cgroup.self"
    own.write_text("0::/cheese-sandboxes/room\n", encoding="utf-8")
    return tmp_path / "cgroup", own


def _room_payload():
    """The payload the backend sends a sandboxed room, as `payload_for` builds
    it, with the mirror probes out of the way: they are another policy's."""
    payload = payload_for(
        uuid.uuid4(),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
        sandbox=True,
        platform_machine=True,
    )
    payload["package_mirrors"] = {"enabled": False}
    return payload


def test_two_cores_of_quota_is_two_workers(tmp_path):
    """The case this exists for: a 2-core sandbox, whose `nproc` says 4."""
    cgroup, own = _cgroup(tmp_path, "200000 100000\n")
    variables = worker_quota.environment({"limits": None}, cgroup=cgroup, own=own)
    assert variables == {
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "2",
        "VITEST_MAX_FORKS": "2",
    }


def test_the_sandboxs_own_limit_is_read_before_any_cgroup(tmp_path):
    """A platform sandbox's quota comes with its launch, and is known before
    the sandbox — and so its cgroup — exists."""
    cgroup, own = _cgroup(tmp_path, "200000 100000\n")
    variables = worker_quota.environment({"cpus": 3}, cgroup=cgroup, own=own)
    assert variables["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "3"
    assert variables["VITEST_MAX_FORKS"] == "3"


def test_no_quota_hands_the_tools_nothing(tmp_path):
    """`cpu.max` of `max` is a room nothing caps: leaving the variables unset
    is the fallback to `nproc`, which is what `auto` and vitest's default
    already size themselves from."""
    cgroup, own = _cgroup(tmp_path, "max 100000\n")
    assert worker_quota.environment({"limits": None}, cgroup=cgroup, own=own) == {}
    assert worker_quota.cores("max 100000") is None


def test_a_quota_one_cgroup_up_is_the_rooms(tmp_path):
    """A leaf cgroup that says `max` is a process whose budget is its parent's:
    reading only the leaf would answer "uncapped" for a room that is capped one
    level up, and keep the oversubscription this exists to prevent."""
    cgroup, own = _cgroup(tmp_path, "max 100000\n")
    (tmp_path / "cgroup/cpu.max").write_text("400000 100000\n", encoding="utf-8")
    assert worker_quota.environment({"limits": None}, cgroup=cgroup, own=own) == {
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "4",
        "VITEST_MAX_FORKS": "4",
    }


def test_a_fraction_of_a_core_is_never_a_fraction_of_a_worker(tmp_path):
    """Quotas are in microseconds and need not divide evenly; the worker count
    rounds down, and never below one."""
    cgroup, own = _cgroup(tmp_path, "150000 100000\n")
    assert worker_quota.environment({"limits": None}, cgroup=cgroup, own=own) == {
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "1",
        "VITEST_MAX_FORKS": "1",
    }
    assert worker_quota.environment({"cpus": 0.5}, cgroup=cgroup, own=own) == {
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "1",
        "VITEST_MAX_FORKS": "1",
    }


def test_a_prepared_room_hands_its_tests_the_quota(tmp_path, monkeypatch):
    """The end of the chain: the environment the executor gives an agent's Bash,
    which is where a `pytest -n auto` in a room actually looks."""
    payload = _room_payload()
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    expected = str(settings.cloud_sandbox_cpus)

    with bootstrap.prepared(payload, tmp_path, fetch_toolchain=False) as (
        _home,
        config,
        _state,
        _env,
    ):
        for name in _NAMES:
            assert config["env"][name] == expected
            assert name not in config["env_reset"]


def test_a_room_that_names_its_own_count_keeps_it(tmp_path, monkeypatch):
    """The same rule the mirrors follow: the platform defaults, it does not
    overrule — a project whose tests need another number sets it."""
    payload = _room_payload()
    payload["environment"] = {
        "variables": {"PYTEST_XDIST_AUTO_NUM_WORKERS": "8"},
    }
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)

    with bootstrap.prepared(payload, tmp_path, fetch_toolchain=False) as (
        _home,
        config,
        _state,
        _env,
    ):
        assert config["env"]["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "8"
        assert config["env"]["VITEST_MAX_FORKS"] == str(settings.cloud_sandbox_cpus)


def test_the_release_carries_the_module_it_needs(tmp_path):
    """The registration a checkout hides.

    `bootstrap.shipped` falls back to the source tree, so a `worker_quota.py`
    missing from `runtime.RELEASE_FILES` still answers from a checkout and only
    fails on the machine — where the release is all there is, and a prepare
    that cannot read the module is a room that does not start. Dropping the
    line from `RELEASE_FILES` turns this red.
    """
    from app.domain.agent.harness.claude_code.remote_execution import runtime

    assert runtime.RELEASE_FILES["remote-execution/worker_quota.py"] == (
        "app/domain/agent/worker_quota.py"
    )
    # Beside the release first, so what a machine runs is what was staged for
    # it and not whatever the checkout the tests run in happens to hold.
    release = tmp_path / "release"
    (release / "remote-execution").mkdir(parents=True)
    (release / "remote-execution/worker_quota.py").write_text(
        'VARIABLES = ("STAGED",)\n', encoding="utf-8"
    )
    assert bootstrap.shipped(release, "worker_quota")["VARIABLES"] == ("STAGED",)
