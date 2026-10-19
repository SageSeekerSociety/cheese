from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.agent_instance.own import connections, naming, ownership
from app.domain.device.login_kind import ConnectionKind


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class NamingDB:
    def __init__(self, project_count):
        self.projects = [uuid4() for _ in range(project_count)]
        self.executions = 0
        self.instances = []

    async def execute(self, _statement):
        self.executions += 1
        if self.executions == 1:
            return Rows([(project_id, 7) for project_id in self.projects])
        instance = SimpleNamespace(configuration={}, display_name=None, created_at=None)
        self.instances.append(instance)
        return Rows([(instance, "device-1", None)])

    async def get(self, _model, _user_id):
        return SimpleNamespace(username="owner", team_id=3)

    async def flush(self):
        pass


@pytest.mark.anyio
async def test_rename_reads_each_owners_nickname_and_logins_once(monkeypatch):
    face_calls = 0
    login_calls = 0

    async def faces_by_handle(_db, handles):
        nonlocal face_calls
        face_calls += 1
        assert list(handles) == ["owner"]
        return {"owner": ("机主", None)}

    async def logins(_db, _owner_user_id):
        nonlocal login_calls
        login_calls += 1
        return [
            connections.Login(
                id="device-1",
                kind=ConnectionKind.subscription,
                plan="pro",
                model=None,
                device_id="device-1",
                device_name="工作机",
            )
        ]

    monkeypatch.setattr(ownership, "faces_by_handle", faces_by_handle)
    monkeypatch.setattr(naming, "logins", logins)

    outputs = []
    for project_count in (3, 8):
        db = NamingDB(project_count)
        await naming.rename_for_connection(db, login_id="device-1")
        outputs.append([instance.display_name for instance in db.instances])
        assert all(name == "机主的 Claude Code（Pro）" for name in outputs[-1])

    assert login_calls == face_calls == 2
    assert outputs[0] == outputs[1][:3]
