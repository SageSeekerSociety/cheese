"""Models and the migrated schema drift apart only less (``alembic check``).

Today the database keeps tables, columns and indexes no model maps any more
(archives, retired features); each is listed in the baseline. A migration that
forgets what its model declares, or a model that forgets what a migration
built, shows up here as a new line.
"""

from app.core.config import settings
from tests.support.schema_debt import load_baseline, measure, ratchet_failures


def test_model_and_migration_drift_only_shrinks(_pg_schema) -> None:
    found = set(measure("drift", settings.database_url)["alembic_check_drift"])
    failures = ratchet_failures(
        "drift", found, set(load_baseline()["alembic_check_drift"])
    )
    assert not failures, "\n".join(failures)
