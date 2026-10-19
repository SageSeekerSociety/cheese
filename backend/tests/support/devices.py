"""Team-scoped device fixtures for suites exercising execution behavior."""

from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.models import DeviceRow
from app.domain.project.models import Project
from app.domain.team.services import team_service


async def enrollment_team(devices, user_id):
    repo = devices._repo
    if isinstance(repo, InMemoryDeviceRepository):
        repo.team_admins.setdefault(1, set()).add(user_id)
        return 1
    return (await team_service(repo._session).ensure_personal_team(user_id)).id


async def place_in_project_team(devices, device_id, project_id, **_):
    """Set fixture ownership before execution; there is no runtime lending API."""
    repo = devices._repo
    if isinstance(repo, InMemoryDeviceRepository):
        device = await repo.get_device(device_id)
        repo.project_teams[project_id] = device.team_id
        return
    project = await repo._session.get(Project, project_id)
    device = await repo._session.get(DeviceRow, device_id)
    device.team_id = project.team_id
    await repo._session.flush()
