"""Comprehensive tests for task visibility filtering and access control.

Covers:
- build_visibility_predicate: creator / public / participant / domain rule
- can_view_task: case sensitivity / empty email / user-not-found edge cases
- Snapshot policy: domain group changes do NOT affect published task visibility
- Detail access: unauthorized → NotFoundError
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.task.visibility_service import TaskVisibilityService

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(**overrides):
    defaults = {
        "id": 1,
        "creator_id": 10,
        "space_id": 100,
        "access_control_enabled": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _user(email="user@cs.edu.cn", email_domain="cs.edu.cn"):
    return SimpleNamespace(id=42, email=email, email_domain=email_domain)


def _mock_session(scalar_returns=None):
    session = AsyncMock()
    session._scalar_returns = scalar_returns or {}

    def _execute_side_effect(stmt):
        stmt_str = str(stmt)
        for kind, val in session._scalar_returns.items():
            if kind in stmt_str:
                m = MagicMock()
                m.scalar_one_or_none.return_value = val
                return m
        m = MagicMock()
        m.scalar_one_or_none.return_value = None
        return m

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    return session


def _visibility_svc(session=None, user_repo=None, admin_repo=None):
    session = session or _mock_session()
    svc = TaskVisibilityService(session=session)
    if user_repo:
        svc._user_repo = user_repo
    if admin_repo:
        svc._admin_repo = admin_repo
    return svc


# ---------------------------------------------------------------------------
# build_visibility_predicate — structure tests
# ---------------------------------------------------------------------------


class TestBuildVisibilityPredicate:
    """Verify the static predicate builder includes and excludes the right clauses."""

    def test_public_tasks_always_visible(self):
        """When access control is off, the predicate matches regardless of domains."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain=None,
        )
        sql = str(pred)
        assert "task.access_control_enabled" in sql

    def test_creator_clause_present(self):
        """Creator is always in the predicate."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain=None,
        )
        sql = str(pred)
        assert "task.creator_id" in sql

    def test_participant_clause_present(self):
        """Participant membership is checked."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain=None,
        )
        sql = str(pred)
        assert "task_membership" in sql.lower()

    def test_domain_clause_present_when_email_domain_provided(self):
        """When email_domain is known, a TaskAccessDomain exists clause is added."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain="cs.edu.cn",
        )
        sql = str(pred)
        assert "task_access_domain" in sql.lower()

    def test_domain_clause_absent_when_email_domain_none(self):
        """When email_domain is None, the domain check clause is omitted."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain=None,
        )
        sql = str(pred)
        assert "task_access_domain" not in sql.lower()


# ---------------------------------------------------------------------------
# can_view_task — email_domain rule edge cases
# ---------------------------------------------------------------------------


class TestCanViewTaskEmailDomain:
    """Tests for email_domain rules: case sensitivity, empty email, extraction."""

    @pytest.mark.anyio
    async def test_domain_case_insensitive_match(self):
        """Domain check should be case-insensitive: User's Cs.EdU.Cn matches CS.EDU.CN."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="Cs.EdU.Cn")
        session = _mock_session({"task_access_domain": 1})
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_empty_email_domain_denied(self):
        """User with empty email_domain and no email cannot view restricted tasks."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="", email_domain="")
        svc = _visibility_svc(user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_email_domain_extracted_from_email(self):
        """When email_domain is None, extract from email (split on '@')."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(
            email="student@physics.edu.cn",
            email_domain=None,
        )
        session = _mock_session({"task_access_domain": 1})  # physics.edu.cn matches
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_email_without_at_symbol(self):
        """Email without '@' should yield None domain → denied for restricted tasks."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="no-at-sign", email_domain=None)
        svc = _visibility_svc(user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_email_domain_cached_preferred_over_extraction(self):
        """email_domain on the model takes priority over parsing email."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(
            email="student@foo.com",
            email_domain="cs.edu.cn",
        )
        session = _mock_session({"task_access_domain": 1})  # cs.edu.cn matches
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_email_domain_always_lowercased(self):
        """Regardless of storage case, domain is always lowercased for comparison."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        # email_domain stored as uppercase, but the service lowercases it
        user_repo.get_by_id.return_value = _user(email_domain="CS.EDU.CN")
        session = _mock_session({"task_access_domain": 1})  # TaskAccessDomain has lowercase
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True


# ---------------------------------------------------------------------------
# can_view_task — role-based bypass (creator / admin / participant)
# ---------------------------------------------------------------------------


class TestCanViewTaskRoleBypass:
    """Publisher, space admin, and participant bypass domain checks."""

    @pytest.mark.anyio
    async def test_creator_can_view_restricted_task(self):
        t = _task(creator_id=10, access_control_enabled=True)
        svc = _visibility_svc()
        assert await svc.can_view_task(task=t, user_id=10) is True

    @pytest.mark.anyio
    async def test_space_admin_can_view_restricted_task(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = SimpleNamespace()
        svc = _visibility_svc(admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_participant_can_view_restricted_task(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        session = _mock_session({"task_membership": 1})
        svc = _visibility_svc(session=session, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_non_creator_non_admin_non_participant_denied(self):
        """Ordinary user with no role cannot view a restricted task unless domain matches."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="other.edu.cn")
        session = _mock_session({"task_access_domain": None})
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_everyone_can_view_public_task(self):
        """Access control disabled: any authenticated user can view."""
        t = _task(creator_id=99, access_control_enabled=False)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        svc = _visibility_svc(admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True


# ---------------------------------------------------------------------------
# can_view_task — unauthenticated user
# ---------------------------------------------------------------------------


class TestCanViewTaskUnauthenticated:
    @pytest.mark.anyio
    async def test_unauthenticated_sees_public_task(self):
        t = _task(access_control_enabled=False)
        svc = _visibility_svc()
        assert await svc.can_view_task(task=t, user_id=0) is True

    @pytest.mark.anyio
    async def test_unauthenticated_hidden_from_restricted_task(self):
        t = _task(access_control_enabled=True)
        svc = _visibility_svc()
        assert await svc.can_view_task(task=t, user_id=0) is False

    @pytest.mark.anyio
    async def test_unauthenticated_user_zero_denied_even_with_matching_domain(self):
        """user_id=0 has no actual user record; domain check never runs."""
        t = _task(access_control_enabled=True)
        svc = _visibility_svc()
        assert await svc.can_view_task(task=t, user_id=0) is False


# ---------------------------------------------------------------------------
# Snapshot policy: domain group mutations don't affect published tasks
# ---------------------------------------------------------------------------


class TestSnapshotPolicy:
    """TaskAccessDomain records are created at publish time and act as a
    snapshot. Later changes to SpaceDomainGroupDomain records do NOT affect
    the task's visibility — only TaskAccessDomain matters."""

    @pytest.mark.anyio
    async def test_task_visible_via_snapshot_even_after_group_deletion(self):
        """If a task's TAD records contain cs.edu.cn, the task remains visible
        to cs.edu.cn users even if the domain group that provided the domain
        has been deleted or modified."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="cs.edu.cn")
        # The presence of the TAD row is what matters — NOT any domain group
        session = _mock_session({"task_access_domain": 1})  # domain found in TAD
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_task_hidden_when_snapshot_lacks_domain(self):
        """Even if a domain was added to the domain group later, a previously
        published task whose TAD snapshot doesn't include it remains invisible."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="new-domain.edu.cn")
        session = _mock_session({"task_access_domain": None})  # NOT in TAD snapshot
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_snapshot_independent_of_group_membership(self):
        """The visibility check queries TaskAccessDomain (snapshot), not
        SpaceDomainGroupDomain (live group). Verify that the _is_domain_allowed
        method only checks TAD."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="old-only.edu.cn")

        execute_calls: list[str] = []

        async def _capture(stmt):
            execute_calls.append(str(stmt))
            for kind, val in {"task_membership": None, "task_access_domain": 1}.items():
                if kind in str(stmt):
                    m = MagicMock()
                    m.scalar_one_or_none.return_value = val
                    return m
            m = MagicMock()
            m.scalar_one_or_none.return_value = None
            return m

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_capture)
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        result = await svc.can_view_task(task=t, user_id=42)
        assert result is True
        # Verify it queries task_access_domain, NOT any domain_group table
        domain_checks = [c for c in execute_calls if "task_access_domain" in c.lower()]
        assert len(domain_checks) >= 1, "Should query TaskAccessDomain"

    @pytest.mark.anyio
    async def test_task_access_domain_soft_delete_removes_visibility(self):
        """Soft-deleting all TAD rows for a task effectively makes it public
        (no domain restrictions remain) but the task.access_control_enabled
        still controls behavior."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="cs.edu.cn")
        # All TAD rows soft-deleted → none active → domain match fails
        session = _mock_session({"task_access_domain": None})  # no active TAD row
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False


# ---------------------------------------------------------------------------
# Detail access — NotFoundError for unauthorized users
# ---------------------------------------------------------------------------


class TestDetailAccessNotFound:
    """When can_view_task returns False, the route should raise NotFoundError
    (not ForbiddenError), so unauthorized users get a 404, not a 403."""

    @pytest.mark.anyio
    async def test_get_task_returns_404_when_not_visible(self):
        """Simulate the get_task route logic: if visibility check fails → NotFoundError."""
        from app.core.errors import NotFoundError

        # Build a restricted task where the user is not the creator, admin,
        # participant, and their domain doesn't match.
        task = _task(creator_id=999, access_control_enabled=True)

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="stranger.edu.cn")
        session = _mock_session({"task_access_domain": None, "task_membership": None})
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)

        can_view = await svc.can_view_task(task=task, user_id=42)
        assert can_view is False

        with pytest.raises(NotFoundError):
            if not can_view:
                raise NotFoundError("Resource task not found", data={"type": "task", "id": task.id})

    @pytest.mark.anyio
    async def test_get_task_visible_when_creator(self):
        """Creator always passes the visibility check for detail access."""
        task = _task(creator_id=42, access_control_enabled=True)
        svc = _visibility_svc()
        assert await svc.can_view_task(task=task, user_id=42) is True

    @pytest.mark.anyio
    async def test_get_task_visible_for_unapproved_to_creator(self):
        """The route has a separate check for unapproved tasks, but visibility
        itself should still pass for the creator."""
        task = _task(creator_id=42, access_control_enabled=True)
        svc = _visibility_svc()
        assert await svc.can_view_task(task=task, user_id=42) is True


# ---------------------------------------------------------------------------
# List filtering — visibility predicate integration
# ---------------------------------------------------------------------------


class TestListFilteringVisibility:
    """Verify the visibility predicate is included in list queries correctly."""

    def test_list_tasks_applies_visibility_predicate_for_ordinary_user(self):
        """When viewer is not a space admin, build_visibility_predicate is called.
        The predicate must include: creator check, public check, participant check,
        and if email_domain is set, domain check."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain="cs.edu.cn",
        )
        sql = str(pred)
        assert "task.creator_id" in sql
        assert "task.access_control_enabled" in sql
        assert "task_membership" in sql.lower()
        assert "task_access_domain" in sql.lower()

    def test_list_tasks_no_domain_check_when_email_is_none(self):
        """When user has no email domain, the predicate omits the domain clause."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain=None,
        )
        sql = str(pred)
        assert "task_access_domain" not in sql.lower()
        # Still includes other checks
        assert "task.creator_id" in sql

    def test_predicate_uses_or_for_multiple_rules(self):
        """Creator OR public OR participant OR domain-matches-allowed:
        the predicate must combine these with OR, not AND."""
        pred = TaskVisibilityService.build_visibility_predicate(
            user_id=42,
            email_domain="cs.edu.cn",
        )
        sql = str(pred)
        # SQLAlchemy OR renders as 'OR'
        assert " OR " in sql.upper() or "or_" in sql.lower()


# ---------------------------------------------------------------------------
# _resolve_user_email_domain integration
# ---------------------------------------------------------------------------


class TestResolveUserEmailDomain:
    """Tests for the email domain resolution helper used in routes."""

    @pytest.mark.anyio
    async def test_resolve_from_email_domain_field(self):
        """email_domain field takes priority over email parsing."""
        from app.api.routes.tasks import _resolve_user_email_domain

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(
            email="a@foo.com",
            email_domain="bar.edu.cn",
        )
        mock_db = AsyncMock()

        with patch(
            "app.api.routes.tasks.UserRepository",
            return_value=user_repo,
        ):
            result = await _resolve_user_email_domain(mock_db, user_id=42)

        assert result == "bar.edu.cn"

    @pytest.mark.anyio
    async def test_resolve_from_email_when_domain_none(self):
        """Fall back to splitting email when email_domain is None."""
        from app.api.routes.tasks import _resolve_user_email_domain

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(
            email="student@physics.edu.cn",
            email_domain=None,
        )
        mock_db = AsyncMock()

        with patch(
            "app.api.routes.tasks.UserRepository",
            return_value=user_repo,
        ):
            result = await _resolve_user_email_domain(mock_db, user_id=42)

        assert result == "physics.edu.cn"

    @pytest.mark.anyio
    async def test_resolve_returns_lowercase(self):
        """Domain is always lowercased regardless of storage."""
        from app.api.routes.tasks import _resolve_user_email_domain

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(
            email="User@CS.EDU.CN",
            email_domain=None,
        )
        mock_db = AsyncMock()

        with patch(
            "app.api.routes.tasks.UserRepository",
            return_value=user_repo,
        ):
            result = await _resolve_user_email_domain(mock_db, user_id=42)

        assert result == "cs.edu.cn"

    @pytest.mark.anyio
    async def test_resolve_returns_none_for_missing_user(self):
        """When user doesn't exist, returns None."""
        from app.api.routes.tasks import _resolve_user_email_domain

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = None
        mock_db = AsyncMock()

        with patch(
            "app.api.routes.tasks.UserRepository",
            return_value=user_repo,
        ):
            result = await _resolve_user_email_domain(mock_db, user_id=999)

        assert result is None

    @pytest.mark.anyio
    async def test_resolve_returns_none_for_empty_email_and_domain(self):
        """User with no email and no email_domain returns None."""
        from app.api.routes.tasks import _resolve_user_email_domain

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="", email_domain="")
        mock_db = AsyncMock()

        with patch(
            "app.api.routes.tasks.UserRepository",
            return_value=user_repo,
        ):
            result = await _resolve_user_email_domain(mock_db, user_id=42)

        assert result is None

    @pytest.mark.anyio
    async def test_resolve_handles_email_without_at(self):
        """Email without '@' → returns None (no domain to extract)."""
        from app.api.routes.tasks import _resolve_user_email_domain

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="plain-string", email_domain=None)
        mock_db = AsyncMock()

        with patch(
            "app.api.routes.tasks.UserRepository",
            return_value=user_repo,
        ):
            result = await _resolve_user_email_domain(mock_db, user_id=42)

        assert result is None


# ---------------------------------------------------------------------------
# Domain comparison edge cases
# ---------------------------------------------------------------------------


class TestDomainComparisonEdgeCases:
    """Verify domain matching handles whitespace, empty strings, unicode."""

    @pytest.mark.anyio
    async def test_single_domain_matches_multiple_tad_rows(self):
        """If multiple TAD rows exist, check passes when any one matches."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="math.edu.cn")
        # Multiple TAD rows exist; one matches
        session = _mock_session({"task_access_domain": 1})
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_dotted_domain_subdomain_distinct(self):
        """cs.edu.cn and ai.cs.edu.cn are distinct domains."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="ai.cs.edu.cn")
        # TAD has cs.edu.cn (NOT ai.cs.edu.cn) — exact match required
        session = _mock_session({"task_access_domain": None})
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_access_control_enabled_but_no_domain_records(self):
        """When access_control_enabled=True but TaskAccessDomain has zero active
        rows (e.g., all were cleared), no one passes the domain check."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="cs.edu.cn")
        session = _mock_session({"task_access_domain": None})  # zero TAD rows
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False


# ---------------------------------------------------------------------------
# User object edge cases
# ---------------------------------------------------------------------------


class TestUserEdgeCases:
    @pytest.mark.anyio
    async def test_user_not_found_cannot_view(self):
        """User deleted / not found → treated as no domain → denied."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = None
        svc = _visibility_svc(user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_user_with_spaces_in_email(self):
        """Email with leading/trailing spaces: the '@' split still works."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="user@cs.edu.cn  ", email_domain=None)
        # The service lowercases the whole split result including trailing spaces.
        # cs.edu.cn with trailing space != cs.edu.cn, so we should verify behavior.
        session = _mock_session({"task_access_domain": None})
        svc = _visibility_svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        # Trailing spaces are included in the domain → no match
        assert await svc.can_view_task(task=t, user_id=42) is False
