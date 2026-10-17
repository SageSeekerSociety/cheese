"""Names, capacity checks and local leases for destructive test resources.

Names alone distinguish workers, not independent pytest runs. The DB fixture
holds a lease until its dependants finish, so another run cannot recreate those
databases mid-test. Pure tests and collection only compute names and never lock.
"""

import fcntl
import hashlib
import json
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

REDIS_DATABASES_PER_SLOT = 16
# Do not use RUNNER_TEMP or a worktree: competing runs must open the same inode.
LOCK_DIRECTORY = Path("/tmp/cheesex-test-slots")


def _slot(value):
    """A slot id safe to paste into a database name, or "" for the only slot."""
    return re.sub(r"\W", "", value or "")


def database_names(worker, slot=""):
    """The (integration, client) database names for one worker of one run."""
    suffix = f"_{worker}" if worker else ""
    prefix = f"cheesex_test_{_slot(slot)}" if _slot(slot) else "cheesex_test"
    return f"{prefix}{suffix}", f"{prefix}{suffix}_c"


def redis_database(worker, base=0, *, database_count=16):
    """Map a worker into its slot's block within the configured server capacity."""
    index = base
    if worker.startswith("gw"):
        index += (int(worker[2:]) + 1) % REDIS_DATABASES_PER_SLOT
    if not 0 <= base <= index < database_count:
        raise ValueError(
            f"Redis database {index} (base {base}) is outside the configured "
            f"Valkey capacity of {database_count} databases (0–{database_count - 1}). "
            "Set CHEESEX_REDIS_DB_COUNT to the target server's actual capacity "
            "(default 16; resident Valkey defaults to 64), and choose a "
            "CHEESE_CI_REDIS_BASE_DB whose worker indices fit. "
            "CHEESEX_TEST_SLOT / CHEESE_CI_SLOT only separates Postgres names."
        )
    return index


def _server(url, default_port):
    parsed = urlsplit(url)
    host = parsed.hostname or "localhost"
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    # Credentials do not distinguish resources and must not enter diagnostics.
    return host, parsed.port or default_port


@contextmanager
def _lease(directory, resource, owner):
    key = hashlib.sha256(json.dumps(resource).encode()).hexdigest()
    # The gate serializes owner changes and shared/exclusive lock conversion.
    # Leave both files in place: unlinking would let a new run lock a new inode.
    with (directory / f"{key}.gate").open("a+") as gate:
        with (directory / f"{key}.lease").open("a+") as lease:
            fcntl.flock(gate, fcntl.LOCK_EX)
            try:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lease.seek(0)
                    if lease.read() != owner:
                        raise RuntimeError(
                            f"Test resource {resource!r} is already occupied by "
                            "another pytest run. Run serially or set a distinct "
                            "CHEESEX_TEST_SLOT (CHEESE_CI_SLOT on CI); Redis also "
                            "needs disjoint CHEESE_CI_REDIS_BASE_DB indices within "
                            "CHEESEX_REDIS_DB_COUNT, or a separate instance."
                        ) from None
                else:
                    lease.seek(0)
                    lease.truncate()
                    lease.write(owner)
                    lease.flush()
                fcntl.flock(lease, fcntl.LOCK_SH)
            finally:
                fcntl.flock(gate, fcntl.LOCK_UN)
            # Closing the descriptor releases the lease, including on exceptions.
            yield


@contextmanager
def claim_slot(*, slot, owner, postgres_urls, redis_url, lock_directory=None):
    """Lease a local run's PG namespaces and Redis index, shared by its workers.

    ``owner`` is pytest-xdist's session-scoped testrun_uid, also available in a
    serial run. Each worker keeps its own descriptor alive through teardown.
    Filesystem leases coordinate processes sharing this host's lock directory,
    not other hosts or containers with separate /tmp mounts.
    """
    if not owner:
        raise ValueError("A test resource lease needs a nonempty run owner")
    directory = LOCK_DIRECTORY if lock_directory is None else Path(lock_directory)
    directory.mkdir(parents=True, exist_ok=True)
    resources = set()
    for url in postgres_urls:
        server = _server(url, 5432)
        resources.add(("postgres-slot", *server, _slot(slot)))
        # An explicit TEST_DATABASE_URL can name the same DB under different slots.
        resources.add(("postgres-db", *server, unquote(urlsplit(url).path)))
    resources.add(("redis-db", *_server(redis_url, 6379), urlsplit(redis_url).path))
    with ExitStack() as leases:
        for resource in sorted(resources):
            leases.enter_context(_lease(directory, resource, owner))
        yield
