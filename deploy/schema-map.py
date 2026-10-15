"""Print every table and column the models in this image map, one per line.

deploy-docker.sh feeds this file on stdin to `python -` inside a standing
connection owner, so it answers with that owner's own code. Whatever it maps
that this release's migrations drop, rename or alter (deploy/migration-touches.py)
breaks the owner's queries, so the owner is released across them (see
`hold_owners_behind_schema`). It imports only what alembic's env.py imports, so
an owner of any age can run it.
"""

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.db import Base

for table in Base.metadata.tables.values():
    print(f"schema-map {table.name}")
    for column in table.columns:
        print(f"schema-map {table.name}.{column.name}")
