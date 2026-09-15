"""Which Postgres databases and which Redis index a test run may use.

Everything the suite isolates is keyed by xdist worker — `cheesex_test_gw3`,
Redis database 4. That is enough while a machine runs one job at a time, because
the names only ever have to be unique within one run.

A pool machine with two runner slots breaks that: two runs of this suite get the
same worker names on the same Postgres and the same Redis, and the harness
creates its databases with `DROP DATABASE ... WITH (FORCE)` — which disconnects
whoever is using it. The second run would delete the first run's databases out
from under it, mid-test.

The slot is what tells two concurrent runs apart. It is empty on a laptop and on
a one-slot machine, where these names are exactly what they were before.
"""

import re

REDIS_DATABASES_PER_SLOT = 16


def _slot(value):
    """A slot id safe to paste into a database name, or "" for the only slot."""
    return re.sub(r"\W", "", value or "")


def database_names(worker, slot=""):
    """The (integration, client) database names for one worker of one run."""
    suffix = f"_{worker}" if worker else ""
    prefix = f"cheesex_test_{_slot(slot)}" if _slot(slot) else "cheesex_test"
    return f"{prefix}{suffix}", f"{prefix}{suffix}_c"


def redis_database(worker, base=0):
    """The Redis database index for one worker of one run.

    Wraps within the slot's own block rather than across it, so a run with more
    workers than the block degrades to sharing inside its own block — never into
    the other slot's, which is the failure this exists to prevent.
    """
    if not worker.startswith("gw"):
        return base
    return base + (int(worker[2:]) + 1) % REDIS_DATABASES_PER_SLOT
