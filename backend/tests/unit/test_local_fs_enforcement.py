"""Getting the grant set onto the machine, and what happens when it is not there.

Two properties are worth a test rather than a comment, because both are the kind of
thing that looks right and behaves wrong:

* the whole live set goes, **both scopes** — filtering by project on this side
  would silently disable project-scoped grants, i.e. the narrower and safer kind,
  leaving only the wider kind working;
* an absent machine never fails the grant — the owner's computer is not always on,
  and 本机离线时项目照常可用 is decided here.
"""

import uuid
from datetime import UTC, datetime

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.local_fs.enforcement import (
    PushOutcome,
    grant_wire,
    plan_local_access,
    push_grants,
)
from app.domain.local_fs.memory_repository import InMemoryLocalFsRepository
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import (
    DirectoryGrant,
    GrantMode,
    GrantScope,
)
from app.domain.local_fs.service import LocalDirectoryService

DEVICE = "device-a1"
OWNER = 7
PROJECT = uuid.uuid4()


class FakeLink:
    """A DeviceHub that never opens a socket. Records what was pushed."""

    def __init__(self, *, online=True, answer=None, raises=None):
        self.online = online
        self.answer = answer
        self.raises = raises
        self.pushed: list[tuple[str, list[dict]]] = []

    def is_online(self, device_id: str) -> bool:
        return self.online

    async def push_local_fs_grants(self, device_id, grants, *, timeout=20):
        self.pushed.append((device_id, grants))
        if self.raises is not None:
            raise self.raises
        if self.answer is not None:
            return self.answer
        return {"fingerprint": "fp-1", "applied": True}


def service_with(repo: InMemoryLocalFsRepository) -> LocalDirectoryService:
    return LocalDirectoryService(repo)


async def test_both_scopes_reach_the_machine():
    """The regression this module was written for.

    A project grant and a user grant are both live; the device is sent both. If
    only the user one arrived, project-scoped grants — the narrower, safer kind
    the UI offers first — would silently never work on any machine, and nothing
    would look wrong.
    """
    repo = InMemoryLocalFsRepository()
    service = service_with(repo)
    await service.grant_directory(
        device_id=DEVICE,
        owner_user_id=OWNER,
        path="/home/alice/Paper",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.PROJECT,
        project_id=PROJECT,
    )
    await service.grant_directory(
        device_id=DEVICE,
        owner_user_id=OWNER,
        path="/home/alice/Shared",
        platform=Platform.LINUX,
        mode=GrantMode.READ_WRITE,
        scope=GrantScope.USER,
    )

    link = FakeLink()
    outcome = await push_grants(service, link, DEVICE)
    assert outcome.delivered is True

    assert len(link.pushed) == 1
    device_id, grants = link.pushed[0]
    assert device_id == DEVICE
    scopes = {g["scope"] for g in grants}
    assert scopes == {"project", "user"}, (
        "both scopes must be sent; filtering by project here would leave the "
        f"narrower kind silently dead, got {scopes}"
    )
    by_scope = {g["scope"]: g for g in grants}
    assert by_scope["project"]["project_id"] == str(PROJECT)
    assert by_scope["user"]["project_id"] is None


async def test_an_offline_machine_does_not_fail_the_grant():
    repo = InMemoryLocalFsRepository()
    service = service_with(repo)
    await service.grant_directory(
        device_id=DEVICE,
        owner_user_id=OWNER,
        path="/home/alice/MyDocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
    )

    link = FakeLink(online=False, raises=DeviceOffline(DEVICE))
    outcome = await push_grants(service, link, DEVICE)

    assert outcome.delivered is False
    assert outcome.reason == "device_offline"
    assert outcome.needs_retry is True
    # The detail is what the owner reads, so it has to say what happened AND what
    # will happen next, not just that something failed.
    assert "不在线" in outcome.detail
    assert "下次" in outcome.detail


async def test_a_machine_that_answers_badly_is_reported_not_raised():
    repo = InMemoryLocalFsRepository()
    service = service_with(repo)
    link = FakeLink(raises=DeviceCallError("boom"))
    outcome = await push_grants(service, link, DEVICE)

    assert outcome.delivered is False
    assert outcome.reason == "device_error"
    assert outcome.needs_retry is True


async def test_a_delivered_set_is_not_retried():
    repo = InMemoryLocalFsRepository()
    service = service_with(repo)
    link = FakeLink()
    outcome = await push_grants(service, link, DEVICE)
    assert outcome.delivered is True
    assert outcome.fingerprint == "fp-1"
    assert outcome.needs_retry is False


async def test_revoking_pushes_an_emptied_set():
    """撤销后立刻失效, on the machine that holds its own copy.

    The device enforces from the set it was last sent, so a revoke that stayed on
    the platform would leave the machine honoring the grant until something else
    happened to re-send the set.
    """
    repo = InMemoryLocalFsRepository()
    service = service_with(repo)
    grant = await service.grant_directory(
        device_id=DEVICE,
        owner_user_id=OWNER,
        path="/home/alice/MyDocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ_WRITE,
        scope=GrantScope.USER,
    )

    link = FakeLink()
    await push_grants(service, link, DEVICE)
    assert len(link.pushed[0][1]) == 1

    await service.revoke(grant.id, owner_user_id=OWNER)
    outcome = await push_grants(service, link, DEVICE)

    assert outcome.delivered is True
    assert link.pushed[-1][1] == [], "a revoke must reach the machine as an empty set"


async def test_a_revoked_grant_is_not_sent_again():
    repo = InMemoryLocalFsRepository()
    service = service_with(repo)
    grant = await service.grant_directory(
        device_id=DEVICE,
        owner_user_id=OWNER,
        path="/home/alice/MyDocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
    )
    await service.revoke(grant.id, owner_user_id=OWNER)
    link = FakeLink()
    await push_grants(service, link, DEVICE)
    assert link.pushed[0][1] == []


def test_the_wire_form_carries_nothing_that_could_be_handed_out():
    """A grant is an access key to somebody's disk. What travels is the minimum the
    machine needs to enforce, and nothing that identifies the owner."""
    grant = DirectoryGrant(
        id=uuid.uuid4(),
        device_id=DEVICE,
        path="/home/alice/MyDocs",
        key="/home/alice/mydocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.PROJECT,
        owner_user_id=OWNER,
        created_at=datetime.now(UTC),
        project_id=PROJECT,
    )
    wire = grant_wire(grant)
    assert set(wire) == {
        "id",
        "path",
        "platform",
        "mode",
        "scope",
        "project_id",
    }
    assert "owner_user_id" not in wire
    assert "key" not in wire


# -- the degradation path -------------------------------------------------


def a_grant():
    return DirectoryGrant(
        id=uuid.uuid4(),
        device_id=DEVICE,
        path="/home/alice/MyDocs",
        key="/home/alice/mydocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
        owner_user_id=OWNER,
        created_at=datetime.now(UTC),
    )


def test_work_with_no_grant_falls_back_without_alarming_anyone():
    """No grant is not a degradation — it is the ordinary case of work that never
    needed a local directory, and there is nothing to tell the person about."""
    plan = plan_local_access(device_online=True, grants=[])
    assert plan.available is False
    assert plan.reason == "no_grant"
    assert plan.fallback == "platform_workspace"


def test_a_grant_on_an_offline_machine_degrades_instead_of_blocking():
    """本机离线时项目照常可用: the work moves to the platform workspace and says
    why. It does not wait for a laptop."""
    plan = plan_local_access(device_online=False, grants=[a_grant()])
    assert plan.available is False
    assert plan.reason == "device_offline"
    assert plan.fallback == "platform_workspace"
    assert "不在线" in plan.detail
    assert "平台工作区" in plan.detail


def test_a_grant_on_an_online_machine_is_available():
    plan = plan_local_access(device_online=True, grants=[a_grant()])
    assert plan.available is True
    assert plan.fallback is None


def test_a_revoked_grant_is_not_a_reason_to_degrade():
    """After a revoke there is nothing to reach for, so the answer is the ordinary
    「no local directory here」 rather than 「your computer is offline」 — which would
    send the owner looking at their machine for a problem that is not there."""
    revoked = DirectoryGrant(
        id=uuid.uuid4(),
        device_id=DEVICE,
        path="/home/alice/MyDocs",
        key="/home/alice/mydocs",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
        owner_user_id=OWNER,
        created_at=datetime.now(UTC),
        revoked_at=datetime.now(UTC),
        revoked_by_user_id=OWNER,
    )
    plan = plan_local_access(device_online=False, grants=[revoked])
    assert plan.reason == "no_grant"


def test_an_outcome_only_asks_for_a_retry_when_it_did_not_land():
    assert PushOutcome(True, "delivered", "").needs_retry is False
    assert PushOutcome(False, "device_offline", "").needs_retry is True
