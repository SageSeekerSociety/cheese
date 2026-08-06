"""The hook WAL is a bind-mounted mailbox shared by two container users."""

import stat
import uuid

from app.core.config import settings
from app.domain.workspace import service as ws


def test_session_dir_precreates_world_writable_hook_spool(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(ws, "_SKILL_SRC", tmp_path / "no-skills")

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    session = ws.session_dir(project_id, topic_id)
    spool = ws.spool_dir(project_id, topic_id)

    assert spool == session / "cheese-spool"
    assert spool.is_dir()
    assert stat.S_IMODE(spool.stat().st_mode) == 0o777
