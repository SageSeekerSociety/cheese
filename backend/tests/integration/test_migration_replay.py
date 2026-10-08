"""A replay database is a copy of its revision, never another test's leftovers.

Data-migration tests copy their starting database from one template per
revision (``revision_template``). A test seeds rows and runs its migration on
its copy; if a copy could carry what an earlier copy wrote, a later test would
start from a scenario it did not seed and could pass or fail for that reason.
"""

from tests.integration.migration_replay import database_at, seed_room
from tests.integration.test_rooms_become_tasks_migration import BEFORE


def test_a_copy_does_not_see_what_an_earlier_copy_of_its_revision_wrote():
    with database_at(BEFORE) as first:
        seed_room(first)
        assert first.fetchval("SELECT count(*) FROM projects") == 1
    with database_at(BEFORE) as second:
        assert second.fetchval("SELECT count(*) FROM projects") == 0
        assert second.fetchval("SELECT count(*) FROM topics") == 0
