"""Print every table and column the models in this image map, one per line.

deploy-docker.sh feeds this file on stdin to `python -` inside an image, so it
answers with that image's own code: the standing connection owner's, and the
release's. Whatever the owner maps that the release no longer does is schema
this release's migrations may drop under it (see `hold_owners_behind_schema`).
It imports only what alembic's env.py imports, so an owner of any age can run it.
"""

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.db import Base

for table in Base.metadata.tables.values():
    print(f"schema-map {table.name}")
    for column in table.columns:
        print(f"schema-map {table.name}.{column.name}")
