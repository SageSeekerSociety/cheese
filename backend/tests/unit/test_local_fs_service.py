"""The 本机目录授权 decision — grants, scope, mode, revocation, audit.

Every case here is a statement about what the owner asked for and what the
system must then do, driven through the public surface (``grant_directory``,
``revoke``, ``authorize``, ``list_access``) against the in-memory repository.
Nothing reaches into the service's internals, so these stay true across a rewrite
of how grants are stored.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.local_fs.memory_repository import InMemoryLocalFsRepository
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import (
    Decision,
    DirectoryGrant,
    GrantMode,
    GrantScope,
)
from app.domain.local_fs.service import (
    AuthorizeRequest,
    GrantRefused,
    LocalDirectoryService,
)

DEVICE = "device-a1"
OWNER = 7
PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()
DOCS = "/home/alice/MyDocs"


@pytest.fixture
def repo() -> InMemoryLocalFsRepository:
    return InMemoryLocalFsRepository()


@pytest.fixture
def service(repo: InMemoryLocalFsRepository) -> LocalDirectoryService:
    return LocalDirectoryService(repo)


def ask(path: str, mode: GrantMode = GrantMode.READ, **kw) -> AuthorizeRequest:
    return AuthorizeRequest(
        device_id=DEVICE,
        path=path,
        platform=Platform.LINUX,
        needed=mode,
        owner_user_id=kw.pop("owner_user_id", OWNER),
        project_id=kw.pop("project_id", PROJECT),
        **kw,
    )


async def grant(
    service: LocalDirectoryService,
    *,
    path: str = DOCS,
    mode: GrantMode = GrantMode.READ,
    scope: GrantScope = GrantScope.PROJECT,
    project_id: uuid.UUID | None = PROJECT,
) -> DirectoryGrant:
    return await service.grant_directory(
        device_id=DEVICE,
        owner_user_id=OWNER,
        path=path,
        platform=Platform.LINUX,
        mode=mode,
        scope=scope,
        project_id=project_id,
    )


# -- the grant itself -------------------------------------------------------


async def test_granted_directory_and_its_contents_can_be_read(service):
    await grant(service)

    assert (await service.authorize(ask(DOCS))).allowed
    assert (await service.authorize(ask(DOCS + "/grades.csv"))).allowed
    assert (await service.authorize(ask(DOCS + "/2026/term1/report.docx"))).allowed


async def test_a_path_outside_every_grant_is_denied_by_name(service):
    await grant(service)

    verdict = await service.authorize(ask("/home/alice/Private/notes.txt"))

    assert not verdict.allowed
    assert verdict.reason == "no_grant"


async def test_a_sibling_that_shares_a_prefix_is_not_inside(service):
    """The bug the whole module exists for, at the level the user sees it."""
    await grant(service, path="/home/alice/MyDocs")

    verdict = await service.authorize(ask("/home/alice/MyDocuments/secret.txt"))

    assert not verdict.allowed
    assert verdict.reason == "no_grant"


async def test_the_whole_disk_cannot_be_granted(service):
    """Criterion one, refused at the door rather than in the UI."""
    with pytest.raises(GrantRefused) as caught:
        await grant(service, path="/")
    assert caught.value.reason == "root_not_grantable"

    with pytest.raises(GrantRefused):
        await grant(service, path="C:/", scope=GrantScope.USER, project_id=None)


async def test_a_relative_path_cannot_be_granted(service):
    with pytest.raises(GrantRefused) as caught:
        await grant(service, path="Documents", scope=GrantScope.USER, project_id=None)
    assert caught.value.reason == "not_absolute"


async def test_granting_the_same_directory_twice_is_one_grant(service):
    first = await grant(service)
    second = await grant(service)
    assert first.id == second.id
    assert len(await service.list_grants(OWNER)) == 1


# -- mode: read separately from read-write ----------------------------------


async def test_a_read_grant_does_not_authorize_a_write(service):
    await grant(service, mode=GrantMode.READ)

    read = await service.authorize(ask(DOCS + "/grades.csv", GrantMode.READ))
    write = await service.authorize(ask(DOCS + "/grades.csv", GrantMode.READ_WRITE))

    assert read.allowed
    assert not write.allowed
    # Named, so the refusal can say which of the two things went wrong.
    assert write.reason == "read_only_grant"


async def test_a_write_grant_also_covers_reading(service):
    await grant(service, mode=GrantMode.READ_WRITE)

    assert (await service.authorize(ask(DOCS + "/a.txt", GrantMode.READ))).allowed
    assert (
        await service.authorize(ask(DOCS + "/a.txt", GrantMode.READ_WRITE))
    ).allowed


async def test_a_narrow_write_grant_does_not_widen_the_read_grant(service):
    """Read the folder, write one subfolder — the ordinary shape of 素材 + 产出."""
    await grant(service, mode=GrantMode.READ)
    await grant(service, path=DOCS + "/out", mode=GrantMode.READ_WRITE)

    inside = await service.authorize(
        ask(DOCS + "/out/result.xlsx", GrantMode.READ_WRITE)
    )
    outside = await service.authorize(
        ask(DOCS + "/grades.csv", GrantMode.READ_WRITE)
    )

    assert inside.allowed
    assert not outside.allowed
    assert outside.reason == "read_only_grant"


# -- scope ------------------------------------------------------------------


async def test_a_user_grant_covers_all_of_the_owners_work(service):
    await grant(service, scope=GrantScope.USER, project_id=None)

    assert (await service.authorize(ask(DOCS + "/a.txt", project_id=PROJECT))).allowed
    assert (
        await service.authorize(ask(DOCS + "/a.txt", project_id=OTHER_PROJECT))
    ).allowed
    assert (await service.authorize(ask(DOCS + "/a.txt", project_id=None))).allowed


async def test_a_project_grant_covers_only_that_project(service):
    await grant(service, scope=GrantScope.PROJECT, project_id=PROJECT)

    assert (await service.authorize(ask(DOCS + "/a.txt", project_id=PROJECT))).allowed

    other = await service.authorize(
        ask(DOCS + "/a.txt", project_id=OTHER_PROJECT)
    )
    assert not other.allowed
    assert other.reason == "no_grant"

    # And work that belongs to no project at all is not the project's to reach.
    loose = await service.authorize(ask(DOCS + "/a.txt", project_id=None))
    assert not loose.allowed


async def test_a_user_grant_may_not_name_a_project(service):
    """The wider scope must not be reachable by mislabelling a narrow one."""
    with pytest.raises(GrantRefused) as caught:
        await grant(service, scope=GrantScope.USER, project_id=PROJECT)
    assert caught.value.reason == "project_forbidden"


async def test_a_project_grant_must_name_a_project(service):
    with pytest.raises(GrantRefused) as caught:
        await grant(service, scope=GrantScope.PROJECT, project_id=None)
    assert caught.value.reason == "project_required"


# -- revocation -------------------------------------------------------------


async def test_revoking_takes_effect_on_the_very_next_question(service):
    """撤销即失效 — no restart, no cache to expire, no window."""
    g = await grant(service, mode=GrantMode.READ_WRITE)
    assert (await service.authorize(ask(DOCS + "/a.txt", GrantMode.READ_WRITE))).allowed

    revoked = await service.revoke(g.id, owner_user_id=OWNER)
    assert revoked is not None and revoked.revoked

    read = await service.authorize(ask(DOCS + "/a.txt", GrantMode.READ))
    write = await service.authorize(ask(DOCS + "/a.txt", GrantMode.READ_WRITE))
    assert not read.allowed and read.reason == "no_grant"
    assert not write.allowed and write.reason == "no_grant"


async def test_a_revoked_grant_is_absent_from_what_the_device_is_sent(service):
    """The push is the revocation mechanism: the device is never re-told about a
    grant that was revoked, so it cannot keep honouring one."""
    keep = await grant(service, path=DOCS)
    drop = await grant(service, path=DOCS + "/out", mode=GrantMode.READ_WRITE)
    before = await service.effective_grants(DEVICE, project_id=PROJECT)
    assert {g.id for g in before.grants} == {keep.id, drop.id}

    await service.revoke(drop.id, owner_user_id=OWNER)
    after = await service.effective_grants(DEVICE, project_id=PROJECT)

    assert {g.id for g in after.grants} == {keep.id}
    # A different set must produce a different fingerprint, or the device would
    # never be re-sent and would keep the revoked grant forever.
    assert after.fingerprint != before.fingerprint


async def test_revoking_someone_elses_grant_changes_nothing(service):
    g = await grant(service)
    assert await service.revoke(g.id, owner_user_id=OWNER + 1) is None
    assert (await service.authorize(ask(DOCS))).allowed


async def test_revoking_twice_is_harmless(service):
    g = await grant(service, scope=GrantScope.USER, project_id=None)
    first = await service.revoke(g.id, owner_user_id=OWNER)
    second = await service.revoke(g.id, owner_user_id=OWNER)
    assert second is not None and second.revoked_at == first.revoked_at


async def test_a_revoked_grant_is_still_listed_for_the_audit(service):
    """The row is kept, not deleted: the trail refers to it."""
    g = await grant(service)
    await service.revoke(g.id, owner_user_id=OWNER)

    assert [x.id for x in await service.list_grants(OWNER)] == []
    with_history = await service.list_grants(OWNER, include_revoked=True)
    assert [x.id for x in with_history] == [g.id]


# -- audit ------------------------------------------------------------------


async def test_every_decision_is_recorded_with_its_coordinates(service):
    """Criterion six: which machine, which directory, for which piece of work,
    and what was actually asked for."""
    g = await grant(service, mode=GrantMode.READ_WRITE)
    topic_id = uuid.uuid4()

    await service.authorize(
        ask(
            DOCS + "/a.txt",
            GrantMode.READ_WRITE,
            actor_handle="cheese",
            topic_id=topic_id,
        )
    )
    await service.authorize(ask("/etc/shadow", GrantMode.READ, actor_handle="cheese"))

    rows = await service.list_access(OWNER)
    assert len(rows) == 2

    denied, allowed = rows[0], rows[1]  # newest first
    assert allowed.decision is Decision.ALLOWED
    assert allowed.path == DOCS + "/a.txt"
    assert allowed.grant_id == g.id
    assert allowed.device_id == DEVICE
    assert allowed.project_id == PROJECT
    assert allowed.topic_id == topic_id
    assert allowed.actor_handle == "cheese"

    assert denied.decision is Decision.DENIED
    assert denied.reason == "no_grant"
    assert denied.path == "/etc/shadow"


async def test_a_refused_path_is_recorded_as_a_refusal_not_an_error(service):
    """A malformed path is still a question with an answer, and the answer is
    still evidence. Raising instead would skip the audit row entirely."""
    verdict = await service.authorize(ask("../../etc/passwd"))

    assert not verdict.allowed
    assert verdict.reason == "not_absolute"
    rows = await service.list_access(OWNER)
    assert [r.decision for r in rows] == [Decision.DENIED]


async def test_the_audit_survives_the_device_being_forgotten(service, repo):
    """The one moment the question actually gets asked is after the machine is
    gone, so the log must not be anchored to it."""
    g = await grant(service)
    await service.authorize(ask(DOCS + "/a.txt"))
    await repo.delete_grants_for_device(DEVICE)

    # The grant is gone, so nothing is authorized any more...
    assert not (await service.authorize(ask(DOCS + "/a.txt"))).allowed
    # ...and the earlier access is still readable, and still says which grant it
    # was made under.
    rows = await service.list_access(OWNER, device_id=DEVICE)
    assert any(r.path == DOCS + "/a.txt" and r.grant_id == g.id for r in rows)


async def test_the_audit_is_readable_per_device(service):
    await grant(service)
    other_device = "device-b2"
    await service.grant_directory(
        device_id=other_device,
        owner_user_id=OWNER,
        path="/home/alice/Other",
        platform=Platform.LINUX,
        mode=GrantMode.READ,
        scope=GrantScope.USER,
    )
    await service.authorize(ask(DOCS + "/a.txt"))
    await service.authorize(
        AuthorizeRequest(
            device_id=other_device,
            path="/home/alice/Other/b.txt",
            platform=Platform.LINUX,
            needed=GrantMode.READ,
            owner_user_id=OWNER,
        )
    )

    only_a = await service.list_access(OWNER, device_id=DEVICE)
    assert {r.device_id for r in only_a} == {DEVICE}
    assert len(only_a) == 1


# -- a grant is not a boundary another owner can cross ---------------------


async def test_a_grant_on_another_persons_machine_is_not_consulted(service, repo):
    """Decisions are per device. A grant somebody else holds on a different
    machine must not make a path readable on this one."""
    await grant(service, path=DOCS)

    stranger_device = "device-stranger"
    stranger = DirectoryGrant(
        id=uuid.uuid4(),
        device_id=stranger_device,
        path=DOCS,
        key=DOCS.casefold(),
        platform=Platform.LINUX,
        mode=GrantMode.READ_WRITE,
        scope=GrantScope.USER,
        owner_user_id=OWNER + 99,
        created_at=datetime.now(UTC),
    )
    await repo.add_grant(stranger)

    on_this_device = await service.authorize(ask(DOCS + "/a.txt"))
    assert on_this_device.allowed
    assert on_this_device.grant is not None
    assert on_this_device.grant.device_id == DEVICE
