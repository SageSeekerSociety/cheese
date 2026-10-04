"""项目设置里存过的「常用配置」，迁移后被拿掉，其余设置原样留着 —— 功能测试.

项目设置的模型不再认 `favorites`（`extra="forbid"`）：一行还带着它的项目一读就
抛，那个项目里的每个房间都开不了工。所以迁移的判据落在读得出来的那一头：跑完之
后，同一批项目经 `project_configs` 读回来，默认还是原来那一个，别的设置一个不少。
"""

import asyncio
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.domain.agent.compute_configs import project_configs
from app.domain.project.models import Project
from tests.integration.conftest import a_team

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_projects_keep_no_compute_favorites.py"
    )
)

DEFAULT = {
    "name": "Lab",
    "profile": "device",
    "device_id": "lab",
}
FAVORITE = {**DEFAULT, "name": "Big cloud", "profile": "cloud", "device_id": None}


def _load():
    spec = importlib.util.spec_from_file_location("_mig_favorites", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_saved_favorites_are_dropped_and_the_rest_of_the_settings_stay(client):
    stored = {
        "with_favorites": {
            "compute_configs": {"default": DEFAULT, "favorites": [FAVORITE]},
            "default_model": "sonnet",
        },
        "default_only": {"compute_configs": {"default": DEFAULT}},
        "never_set": {"default_model": "sonnet"},
    }
    ids = {}

    async def seed() -> None:
        async with client.test_factory() as s:
            team_id = await a_team(s)
            for key, value in stored.items():
                project = Project(team_id=team_id, name=key, owner_handle="alice")
                s.add(project)
                await s.flush()
                ids[key] = project.id
                # 写库的是原始 JSON：带 `favorites` 的那一份现在的模型已经收不进来。
                await s.execute(
                    text(
                        "UPDATE projects SET settings = CAST(:v AS json) WHERE id = :id"
                    ),
                    {"v": json.dumps(value), "id": project.id},
                )
            await s.commit()

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().upgrade()

    async def upgrade_and_read() -> dict:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(apply)
            await s.commit()
        async with client.test_factory() as s:
            return {
                key: (await s.get(Project, pid)).settings for key, pid in ids.items()
            }

    asyncio.run(seed())
    after = asyncio.run(upgrade_and_read())

    assert after["with_favorites"] == {
        "compute_configs": {"default": DEFAULT},
        "default_model": "sonnet",
    }
    assert after["default_only"] == stored["default_only"]
    assert after["never_set"] == stored["never_set"]
    # 读得出来：原来那个默认原样回来。
    assert project_configs(after["with_favorites"]).default.name == "Lab"
