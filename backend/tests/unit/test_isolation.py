"""Independent runs must not recreate resources another run is still using."""

import json
import os
import select
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests import isolation

BACKEND = Path(__file__).resolve().parents[2]


def test_one_slot_keeps_the_names_it_always_had():
    assert isolation.database_names("gw3") == ("cheesex_test_gw3", "cheesex_test_gw3_c")
    assert isolation.database_names("") == ("cheesex_test", "cheesex_test_c")
    assert isolation.redis_database("gw3") == 4
    assert isolation.redis_database("") == 0


def test_two_slots_on_one_machine_share_no_database():
    first = set(
        isolation.database_names("gw0", "a") + isolation.database_names("gw7", "a")
    )
    second = set(
        isolation.database_names("gw0", "b") + isolation.database_names("gw7", "b")
    )
    assert not first & second


def test_resident_capacity_allows_a_second_redis_block():
    first = {
        isolation.redis_database(f"gw{n}", 0, database_count=64) for n in range(16)
    }
    second = {
        isolation.redis_database(f"gw{n}", 16, database_count=64) for n in range(16)
    }
    assert first == set(range(16))
    assert second == set(range(16, 32))
    assert isolation.redis_database("gw14", 48, database_count=64) == 63


@pytest.mark.parametrize(
    "worker,base,count",
    [
        ("", 16, 16),
        ("gw0", 16, 16),
        ("gw0", 15, 16),
        ("", -1, 16),
        ("gw0", -1, 16),
        ("gw14", 49, 64),
        ("", 0, 0),
    ],
)
def test_out_of_capacity_fails_with_configuration_guidance(worker, base, count):
    with pytest.raises(ValueError) as error:
        isolation.redis_database(worker, base, database_count=count)
    message = str(error.value)
    assert str(count) in message
    assert "CHEESEX_REDIS_DB_COUNT" in message
    assert "CHEESE_CI_REDIS_BASE_DB" in message


def test_a_slot_id_cannot_smuggle_anything_into_a_database_name():
    name, _client = isolation.database_names("gw0", 'a"; DROP DATABASE x --')
    assert name == "cheesex_test_aDROPDATABASEx_gw0"


def _resources(tmp_path, *, owner="run-a", slot="a", worker="", redis_db=0, port=5432):
    return dict(
        slot=slot,
        owner=owner,
        postgres_urls=[
            f"postgresql://user:secret@localhost:{port}/{name}"
            for name in isolation.database_names(worker, slot)
        ],
        redis_url=f"redis://localhost:6379/{redis_db}",
        lock_directory=str(tmp_path / "leases"),
    )


@contextmanager
def _holder(resources):
    program = """
import json, sys
from tests.isolation import claim_slot
with claim_slot(**json.loads(sys.argv[1])):
    print('held', flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", program, json.dumps(resources)],
        cwd=BACKEND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert select.select([child.stdout], [], [], 10)[0], (
                "lease process did not start"
            )
            assert child.stdout.readline().strip() == "held", child.stderr.read()
            yield child
        finally:
            if child.poll() is None:
                child.stdin.close()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def test_another_process_cannot_claim_the_same_slot(tmp_path):
    with _holder(_resources(tmp_path)):
        # Different worker and Redis index still share the Postgres slot.
        with pytest.raises(RuntimeError, match="CHEESEX_TEST_SLOT") as error:
            with isolation.claim_slot(
                **_resources(
                    tmp_path,
                    owner="run-b",
                    worker="gw1",
                    redis_db=2,
                )
            ):
                pytest.fail("a second run was allowed to recreate the slot")
        assert "secret" not in str(error.value)


def test_workers_of_one_run_can_share_the_slot(tmp_path):
    with _holder(_resources(tmp_path)):
        with isolation.claim_slot(**_resources(tmp_path, worker="gw1", redis_db=2)):
            pass


@pytest.mark.parametrize("slot,redis_db,port", [("b", 16, 5432), ("a", 16, 5442)])
def test_disjoint_resources_do_not_block_each_other(tmp_path, slot, redis_db, port):
    with _holder(_resources(tmp_path)):
        with isolation.claim_slot(
            **_resources(
                tmp_path,
                owner="run-b",
                slot=slot,
                redis_db=redis_db,
                port=port,
            )
        ):
            pass


def test_a_different_slot_does_not_hide_a_redis_collision(tmp_path):
    with _holder(_resources(tmp_path)):
        with pytest.raises(RuntimeError, match="redis-db"):
            with isolation.claim_slot(**_resources(tmp_path, owner="run-b", slot="b")):
                pytest.fail("two runs shared a Redis index")


def test_cleaned_slot_names_and_explicit_databases_still_collide(tmp_path):
    with _holder(_resources(tmp_path)):
        with pytest.raises(RuntimeError):
            with isolation.claim_slot(**_resources(tmp_path, owner="run-b", slot="a!")):
                pytest.fail("slot spelling bypassed the lease")
        other = _resources(tmp_path, owner="run-b", slot="b", redis_db=16)
        other["postgres_urls"] = _resources(tmp_path)["postgres_urls"]
        with pytest.raises(RuntimeError, match="postgres-db"):
            with isolation.claim_slot(**other):
                pytest.fail("an explicit database bypassed the slot lease")


@pytest.mark.parametrize("killed", [False, True])
def test_process_exit_releases_ownership(tmp_path, killed):
    with _holder(_resources(tmp_path)) as child:
        if killed:
            child.kill()
            child.wait(timeout=5)
    with isolation.claim_slot(**_resources(tmp_path, owner="run-b")):
        pass


def test_a_failed_multi_resource_claim_releases_earlier_leases(tmp_path):
    with _holder(_resources(tmp_path)):
        with pytest.raises(RuntimeError, match="redis-db"):
            with isolation.claim_slot(**_resources(tmp_path, owner="run-b", slot="b")):
                pass
        with isolation.claim_slot(
            **_resources(tmp_path, owner="run-c", slot="b", redis_db=16)
        ):
            pass


def _probe(tmp_path, *, workers=0):
    """Run the real fixture graph with only the expensive DB boundary replaced."""
    plugin = textwrap.dedent(f"""
        from pathlib import Path
        import os
        import pytest
        from tests import conftest as harness, isolation

        ROOT = Path({str(tmp_path)!r})
        harness._UNIT_DIR = str(ROOT) + os.sep
        isolation.LOCK_DIRECTORY = ROOT / 'leases'
        harness._ensure_template = lambda: False
        harness.drop_connections_left_in_the_app_pool = lambda: None
        harness._empty_the_job_clock = lambda: None

        def assert_occupied():
            with pytest.raises(RuntimeError, match='already occupied'):
                with isolation.claim_slot(
                    slot=harness._SLOT, owner='independent-run',
                    postgres_urls=(
                        harness.settings.database_url, harness.TEST_DATABASE_URL,
                    ),
                    redis_url=harness.settings.redis_url,
                ):
                    pass

        def create(name, url):
            assert_occupied()
            (ROOT / ('created-' + name)).touch()

        harness._create_and_migrate = create
        _database_slot = harness._database_slot
        _pg_schema = harness._pg_schema
        _pg_schema_gate = harness._pg_schema_gate

        @pytest.fixture
        def database_user(_pg_schema):
            yield
            assert_occupied()
    """)
    if workers:
        # Collection/serial probes cover the controller's import path separately.
        # Avoid importing the entire app in a controller that executes no fixture.
        plugin = (
            "import os\nif os.environ.get('PYTEST_XDIST_WORKER'):\n"
            + textwrap.indent(plugin, "    ")
        )
    (tmp_path / "conftest.py").write_text(plugin)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("PYTEST_") or key in {
            "CHEESE_CI_SLOT",
            "CHEESE_CI_REDIS_BASE_DB",
            "CHEESE_CI_VALKEY_DATABASES",
            "TEST_DATABASE_URL",
        }:
            env.pop(key)
    env.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONPATH=str(BACKEND),
        CHEESEX_TEST_SLOT="probe",
        CHEESEX_REDIS_DB_COUNT="16",
        TEST_PG_BASE="postgresql+asyncpg://postgres:postgres@localhost:59999",
        REDIS_URL="redis://localhost:61999/0",
    )
    return env


def _pytest_probe(tmp_path, env, *args, expected_status=0):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "-p",
            "xdist.plugin",
            "-p",
            "no:cacheprovider",
            "-q",
            *args,
            str(tmp_path / "test_probe.py"),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == expected_status, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("workers", [0, 2])
def test_real_fixture_locks_before_creation_through_teardown(tmp_path, workers):
    env = _probe(tmp_path, workers=workers)
    env["CHEESE_CI_SLOT"] = "fallback"
    (tmp_path / "test_probe.py").write_text(
        textwrap.dedent(f"""
        import time
        from pathlib import Path
        import pytest

        @pytest.mark.parametrize('n', [0, 1])
        def test_database(database_user, n):
            if {bool(workers)!r}:
                root = Path(__file__).parent
                (root / f'ready-{{n}}').touch()
                deadline = time.monotonic() + 5
                while len(list(root.glob('ready-*'))) < 2:
                    assert time.monotonic() < deadline, 'workers could not share'
                    time.sleep(0.01)
    """)
    )
    _pytest_probe(tmp_path, env, "-n", str(workers))
    suffixes = ("_gw0", "_gw1") if workers else ("",)
    assert {path.name for path in tmp_path.glob("created-*")} == {
        f"created-cheesex_test_probe{worker}{client}"
        for worker in suffixes
        for client in ("", "_c")
    }
    # The whole fixture graph finished; a subsequent run can take the slot.
    with isolation.claim_slot(
        slot="probe",
        owner="next-run",
        postgres_urls=["postgresql://postgres@localhost:59999/cheesex_test_probe"],
        redis_url="redis://localhost:61999/0",
        lock_directory=tmp_path / "leases",
    ):
        pass


def test_competing_run_stops_before_any_database_is_recreated(tmp_path):
    env = _probe(tmp_path)
    (tmp_path / "test_probe.py").write_text("def test_db(database_user): pass\n")
    with isolation.claim_slot(
        slot="probe",
        owner="already-running",
        postgres_urls=["postgresql://postgres@localhost:59999/cheesex_test_probe"],
        redis_url="redis://localhost:61999/0",
        lock_directory=tmp_path / "leases",
    ):
        output = _pytest_probe(tmp_path, env, expected_status=1)
    assert "CHEESEX_TEST_SLOT" in output
    assert not list(tmp_path.glob("created-*"))


@pytest.mark.parametrize("collect_only", [False, True])
def test_pure_tests_and_collection_do_not_claim_resources(tmp_path, collect_only):
    env = _probe(tmp_path)
    argument = "database_user" if collect_only else ""
    (tmp_path / "test_probe.py").write_text(f"def test_probe({argument}): pass\n")
    with isolation.claim_slot(
        slot="probe",
        owner="already-running",
        postgres_urls=["postgresql://postgres@localhost:59999/cheesex_test_probe"],
        redis_url="redis://localhost:61999/0",
        lock_directory=tmp_path / "leases",
    ):
        _pytest_probe(tmp_path, env, *(["--collect-only"] if collect_only else []))
    assert not list(tmp_path.glob("created-*"))


@pytest.mark.parametrize(
    "variable", ["CHEESEX_REDIS_DB_COUNT", "CHEESE_CI_VALKEY_DATABASES"]
)
@pytest.mark.parametrize("count", [16, 64])
def test_harness_uses_the_declared_instance_capacity(tmp_path, count, variable):
    env = _probe(tmp_path)
    env.pop("CHEESEX_REDIS_DB_COUNT")
    # The explicit override must win even when the resident setting disagrees.
    env["CHEESE_CI_VALKEY_DATABASES"] = "64" if count == 16 else "16"
    env.update({variable: str(count), "CHEESE_CI_REDIS_BASE_DB": "16"})
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests import conftest; assert conftest._REDIS_DB == 16",
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if count == 64:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
        assert "CHEESEX_REDIS_DB_COUNT" in result.stderr
        assert "CHEESE_CI_REDIS_BASE_DB" in result.stderr
