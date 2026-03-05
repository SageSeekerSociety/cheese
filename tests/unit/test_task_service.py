"""Unit tests for TaskService, TaskMembershipService, TaskSubmissionService, and TaskSubmissionReviewService."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.domain_errors import (
    TaskParticipantsReachedLimitError,
    TeamSizeNotEnoughError,
    TeamSizeTooLargeError,
)
from app.core.errors import BadRequestError, NotFoundError
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionReviewService,
    TaskSubmissionService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC).replace(tzinfo=None)


def _make_task(**overrides):
    defaults = {
        "id": 1,
        "name": "Test Task",
        "intro": "intro",
        "description": "desc",
        "creator_id": 10,
        "space_id": 100,
        "category_id": 5,
        "submitter_type": 0,  # USER type
        "approved": 0,  # APPROVED
        "participant_limit": None,
        "deadline": None,
        "registration_start_at": None,
        "default_deadline": 0,
        "resubmittable": False,
        "editable": True,
        "rank": None,
        "require_real_name": False,
        "min_team_size": None,
        "max_team_size": None,
        "reject_reason": "",
        "team_locking_policy": "NO_LOCK",
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_membership(**overrides):
    defaults = {
        "id": 50,
        "task_id": 1,
        "member_id": 42,
        "approved": 0,
        "is_team": False,
        "email": "test@example.com",
        "phone": "123",
        "completion_status": "NOT_SUBMITTED",
        "created_at": _NOW,
        "updated_at": _NOW,
        "deadline": None,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_submission(**overrides):
    defaults = {
        "id": 200,
        "membership_id": 50,
        "version": 1,
        "submitter_id": 42,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_entry(**overrides):
    defaults = {
        "id": 300,
        "task_submission_id": 200,
        "index": 0,
        "content_text": "Answer text",
        "content_attachment_id": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_review(**overrides):
    defaults = {
        "id": 400,
        "submission_id": 200,
        "accepted": True,
        "score": 90,
        "comment": "Good work",
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# TaskService
# ---------------------------------------------------------------------------


class TestTaskService:
    @pytest.mark.anyio
    async def test_get_task_found(self):
        repo = AsyncMock()
        task = _make_task()
        repo.get_by_id.return_value = task

        svc = TaskService(repo)
        result = await svc.get_task(1)

        assert result is task
        repo.get_by_id.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_get_task_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = TaskService(repo)
        result = await svc.get_task(999)

        assert result is None

    @pytest.mark.anyio
    async def test_enumerate_tasks_delegates_to_repo(self):
        repo = AsyncMock()
        tasks = [_make_task(id=1), _make_task(id=2)]
        repo.list_tasks.return_value = tasks

        svc = TaskService(repo)
        result = await svc.enumerate_tasks(space_id=100, limit=20, offset=0)

        assert result == tasks
        repo.list_tasks.assert_awaited_once()
        call_kwargs = repo.list_tasks.call_args.kwargs
        assert call_kwargs["space_id"] == 100

    @pytest.mark.anyio
    async def test_count_tasks_delegates_to_repo(self):
        repo = AsyncMock()
        repo.count_tasks.return_value = 7

        svc = TaskService(repo)
        result = await svc.count_tasks(space_id=100)

        assert result == 7
        repo.count_tasks.assert_awaited_once()


# ---------------------------------------------------------------------------
# TaskMembershipService
# ---------------------------------------------------------------------------


class TestTaskMembershipService:
    def _build_service(
        self,
        repo=None,
        realname_repo=None,
        space_repo=None,
        space_rank_repo=None,
    ):
        repo = repo or AsyncMock()
        return TaskMembershipService(
            repo=repo,
            realname_repo=realname_repo,
            space_repo=space_repo,
            space_rank_repo=space_rank_repo,
        ), repo

    @pytest.mark.anyio
    async def test_create_membership_happy_path(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.get_by_task_and_member.return_value = None
        repo.save.side_effect = lambda m: m

        svc, _ = self._build_service(repo=repo)
        task = _make_task(participant_limit=10)

        result = await svc.create_membership(
            task=task,
            member_id=42,
            is_team=False,
            approved=0,
            deadline=None,
            email="a@b.com",
            phone="555",
            apply_reason=None,
            personal_advantage=None,
            remark=None,
        )

        assert result.task_id == 1
        assert result.member_id == 42
        assert result.approved == 0
        repo.save.assert_awaited_once()

    @pytest.mark.anyio
    async def test_create_membership_raises_when_limit_reached(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 5
        repo.get_by_task_and_member.return_value = None

        svc, _ = self._build_service(repo=repo)
        task = _make_task(participant_limit=5, auto_reject_when_full=False)

        with pytest.raises(BadRequestError, match="participant limit reached"):
            await svc.create_membership(
                task=task,
                member_id=42,
                is_team=False,
                approved=0,
                deadline=None,
                email=None,
                phone=None,
                apply_reason=None,
                personal_advantage=None,
                remark=None,
            )

    @pytest.mark.anyio
    async def test_create_membership_auto_reject_when_full(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 5
        repo.get_by_task_and_member.return_value = None
        repo.save.side_effect = lambda m: m

        svc, _ = self._build_service(repo=repo)
        task = _make_task(participant_limit=5, auto_reject_when_full=True)

        result = await svc.create_membership(
            task=task,
            member_id=42,
            is_team=False,
            approved=0,
            deadline=None,
            email=None,
            phone=None,
            apply_reason=None,
            personal_advantage=None,
            remark=None,
        )

        # approved should have been changed to DISAPPROVED (1)
        assert result.approved == 1

    @pytest.mark.anyio
    async def test_create_membership_rejects_duplicate(self):
        repo = AsyncMock()
        existing = _make_membership()
        repo.get_by_task_and_member.return_value = existing

        svc, _ = self._build_service(repo=repo)
        task = _make_task()

        with pytest.raises(BadRequestError, match="already participating"):
            await svc.create_membership(
                task=task,
                member_id=42,
                is_team=False,
                approved=0,
                deadline=None,
                email=None,
                phone=None,
                apply_reason=None,
                personal_advantage=None,
                remark=None,
            )

    @pytest.mark.anyio
    async def test_create_membership_requires_real_name(self):
        repo = AsyncMock()
        repo.get_by_task_and_member.return_value = None

        realname_repo = AsyncMock()
        realname_repo.has_identity.return_value = False

        svc, _ = self._build_service(repo=repo, realname_repo=realname_repo)
        task = _make_task(require_real_name=True)

        with pytest.raises(BadRequestError, match="real name"):
            await svc.create_membership(
                task=task,
                member_id=42,
                is_team=False,
                approved=0,
                deadline=None,
                email=None,
                phone=None,
                apply_reason=None,
                personal_advantage=None,
                remark=None,
            )

    @pytest.mark.anyio
    async def test_soft_delete_membership(self):
        repo = AsyncMock()
        repo.save.side_effect = lambda m: m
        membership = _make_membership()

        svc, _ = self._build_service(repo=repo)
        await svc.soft_delete_membership(membership)

        assert membership.deleted_at is not None
        repo.save.assert_awaited_once()

    @pytest.mark.anyio
    async def test_update_membership_approve_checks_participant_limit(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 5
        repo.save.side_effect = lambda m: m

        svc, _ = self._build_service(repo=repo)
        task = _make_task(participant_limit=5)
        membership = _make_membership(approved=2)  # was NONE, now approving to 0

        with pytest.raises(TaskParticipantsReachedLimitError):
            await svc.update_membership(
                membership=membership,
                task=task,
                approved=0,
            )

    @pytest.mark.anyio
    async def test_update_membership_approve_team_checks_size(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.count_team_members.return_value = 1
        repo.save.side_effect = lambda m: m

        svc, _ = self._build_service(repo=repo)
        task = _make_task(min_team_size=3, max_team_size=5)
        membership = _make_membership(approved=2, is_team=True)

        with pytest.raises(TeamSizeNotEnoughError):
            await svc.update_membership(
                membership=membership,
                task=task,
                approved=0,
            )

    @pytest.mark.anyio
    async def test_update_membership_approve_team_too_large(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.count_team_members.return_value = 10
        repo.save.side_effect = lambda m: m

        svc, _ = self._build_service(repo=repo)
        task = _make_task(min_team_size=2, max_team_size=5)
        membership = _make_membership(approved=2, is_team=True)

        with pytest.raises(TeamSizeTooLargeError):
            await svc.update_membership(
                membership=membership,
                task=task,
                approved=0,
            )

    @pytest.mark.anyio
    async def test_update_membership_approve_user_real_name_required(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.save.side_effect = lambda m: m

        realname_repo = AsyncMock()
        realname_repo.has_identity.return_value = False

        svc, _ = self._build_service(repo=repo, realname_repo=realname_repo)
        task = _make_task(require_real_name=True)
        membership = _make_membership(approved=2, is_team=False)

        with pytest.raises(BadRequestError, match="real name"):
            await svc.update_membership(
                membership=membership,
                task=task,
                approved=0,
            )

    @pytest.mark.anyio
    async def test_update_membership_happy_path(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.save.side_effect = lambda m: m

        svc, _ = self._build_service(repo=repo)
        task = _make_task()
        membership = _make_membership(approved=2)

        result = await svc.update_membership(
            membership=membership,
            task=task,
            approved=0,
            email="new@mail.com",
        )

        assert result.approved == 0
        assert result.email == "new@mail.com"

    @pytest.mark.anyio
    async def test_eligibility_deleted_task(self):
        repo = AsyncMock()
        svc, _ = self._build_service(repo=repo)
        task = _make_task(deleted_at=_NOW)

        result = await svc.get_participation_eligibility(task=task, user_id=42)

        assert result["user"]["eligible"] is False

    @pytest.mark.anyio
    async def test_eligibility_user_type_eligible(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.get_user_membership.return_value = None

        svc, _ = self._build_service(repo=repo)
        task = _make_task(submitter_type=0, approved=0)

        result = await svc.get_participation_eligibility(task=task, user_id=42)

        assert result["user"]["eligible"] is True
        assert result["user"]["reasons"] == []
        assert result["teams"] is None

    @pytest.mark.anyio
    async def test_eligibility_user_already_participating(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 0
        repo.get_user_membership.return_value = _make_membership()

        svc, _ = self._build_service(repo=repo)
        task = _make_task(submitter_type=0, approved=0)

        result = await svc.get_participation_eligibility(task=task, user_id=42)

        assert result["user"]["eligible"] is False
        codes = [r["code"] for r in result["user"]["reasons"]]
        assert "ALREADY_PARTICIPATING" in codes

    @pytest.mark.anyio
    async def test_eligibility_user_participant_limit_reached(self):
        repo = AsyncMock()
        repo.count_approved_for_task.return_value = 10
        repo.get_user_membership.return_value = None

        svc, _ = self._build_service(repo=repo)
        task = _make_task(submitter_type=0, approved=0, participant_limit=10)

        result = await svc.get_participation_eligibility(task=task, user_id=42)

        assert result["user"]["eligible"] is False
        codes = [r["code"] for r in result["user"]["reasons"]]
        assert "PARTICIPANT_LIMIT_REACHED" in codes


# ---------------------------------------------------------------------------
# TaskSubmissionService
# ---------------------------------------------------------------------------


class TestTaskSubmissionService:
    def _build_service(
        self,
        submission_repo=None,
        entry_repo=None,
        review_repo=None,
        membership_repo=None,
    ):
        submission_repo = submission_repo or AsyncMock()
        entry_repo = entry_repo or AsyncMock()
        review_repo = review_repo or AsyncMock()
        membership_repo = membership_repo or AsyncMock()
        return TaskSubmissionService(
            submission_repo=submission_repo,
            entry_repo=entry_repo,
            review_repo=review_repo,
            membership_repo=membership_repo,
        )

    @pytest.mark.anyio
    async def test_submit_task_happy_path(self):
        membership = _make_membership(id=50)
        submission = _make_submission(id=200, membership_id=50, version=1)
        entry = _make_entry(task_submission_id=200, index=0)

        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = [membership]

        submission_repo = AsyncMock()
        submission_repo.get_latest_version_for_membership.return_value = 0
        submission_repo.create_submission.return_value = submission

        entry_repo = AsyncMock()
        entry_repo.list_by_submission_id.return_value = [entry]

        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(
            submission_repo=submission_repo,
            entry_repo=entry_repo,
            review_repo=review_repo,
            membership_repo=membership_repo,
        )

        result = await svc.submit_task(
            task_id=1,
            participant_id=50,
            submitter_id=42,
            contents=[{"text": "My answer"}],
        )

        assert result["id"] == 200
        assert result["version"] == 1
        assert result["member"]["id"] == 42
        assert result["submitter"]["id"] == 42
        assert len(result["content"]) == 1
        submission_repo.create_submission.assert_awaited_once_with(
            membership_id=50,
            submitter_id=42,
            version=1,
        )
        entry_repo.create_entries.assert_awaited_once()

    @pytest.mark.anyio
    async def test_submit_task_participant_not_found(self):
        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = []

        svc = self._build_service(membership_repo=membership_repo)

        with pytest.raises(NotFoundError):
            await svc.submit_task(
                task_id=1,
                participant_id=999,
                submitter_id=42,
                contents=[{"text": "answer"}],
            )

    @pytest.mark.anyio
    async def test_submit_task_version_bumps(self):
        membership = _make_membership(id=50)
        submission = _make_submission(id=201, membership_id=50, version=3)

        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = [membership]

        submission_repo = AsyncMock()
        submission_repo.get_latest_version_for_membership.return_value = 2
        submission_repo.create_submission.return_value = submission

        entry_repo = AsyncMock()
        entry_repo.list_by_submission_id.return_value = []

        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(
            submission_repo=submission_repo,
            entry_repo=entry_repo,
            review_repo=review_repo,
            membership_repo=membership_repo,
        )

        await svc.submit_task(
            task_id=1,
            participant_id=50,
            submitter_id=42,
            contents=[],
        )

        submission_repo.create_submission.assert_awaited_once_with(
            membership_id=50,
            submitter_id=42,
            version=3,
        )

    @pytest.mark.anyio
    async def test_modify_submission_happy_path(self):
        membership = _make_membership(id=50)
        submission = _make_submission(id=200, membership_id=50, version=1)
        new_entry = _make_entry(content_text="Updated text")

        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = [membership]

        submission_repo = AsyncMock()
        submission_repo.get_by_membership_and_version.return_value = submission
        submission_repo.save.side_effect = lambda s: s

        entry_repo = AsyncMock()
        entry_repo.list_by_submission_id.return_value = [new_entry]

        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(
            submission_repo=submission_repo,
            entry_repo=entry_repo,
            review_repo=review_repo,
            membership_repo=membership_repo,
        )

        result = await svc.modify_submission(
            task_id=1,
            participant_id=50,
            submitter_id=42,
            version=1,
            contents=[{"text": "Updated text"}],
        )

        assert result["id"] == 200
        entry_repo.soft_delete_by_membership_and_version.assert_awaited_once_with(
            membership_id=50,
            version=1,
        )
        entry_repo.create_entries.assert_awaited_once()

    @pytest.mark.anyio
    async def test_modify_submission_not_found(self):
        membership = _make_membership(id=50)

        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = [membership]

        submission_repo = AsyncMock()
        submission_repo.get_by_membership_and_version.return_value = None

        svc = self._build_service(
            submission_repo=submission_repo,
            membership_repo=membership_repo,
        )

        with pytest.raises(NotFoundError):
            await svc.modify_submission(
                task_id=1,
                participant_id=50,
                submitter_id=42,
                version=99,
                contents=[],
            )

    @pytest.mark.anyio
    async def test_list_submissions(self):
        membership = _make_membership(id=50)
        submission = _make_submission(id=200, membership_id=50)
        entry = _make_entry()

        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = [membership]

        submission_repo = AsyncMock()
        submission_repo.list_submissions.return_value = [submission]
        submission_repo.count_submissions.return_value = 1

        entry_repo = AsyncMock()
        entry_repo.list_by_submission_id.return_value = [entry]

        review_repo = AsyncMock()

        svc = self._build_service(
            submission_repo=submission_repo,
            entry_repo=entry_repo,
            review_repo=review_repo,
            membership_repo=membership_repo,
        )

        items, total = await svc.list_submissions(
            task_id=1,
            limit=20,
        )

        assert total == 1
        assert len(items) == 1
        assert items[0]["id"] == 200

    @pytest.mark.anyio
    async def test_submission_dto_file_entry(self):
        """Verify that entries with an attachment_id are typed as FILE."""
        membership = _make_membership(id=50)
        submission = _make_submission(id=200, membership_id=50)
        file_entry = _make_entry(content_text=None, content_attachment_id=77)

        membership_repo = AsyncMock()
        membership_repo.list_memberships_for_task.return_value = [membership]

        submission_repo = AsyncMock()
        submission_repo.get_latest_version_for_membership.return_value = 0
        submission_repo.create_submission.return_value = submission

        entry_repo = AsyncMock()
        entry_repo.list_by_submission_id.return_value = [file_entry]

        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(
            submission_repo=submission_repo,
            entry_repo=entry_repo,
            review_repo=review_repo,
            membership_repo=membership_repo,
        )

        result = await svc.submit_task(
            task_id=1,
            participant_id=50,
            submitter_id=42,
            contents=[{"attachmentId": "77"}],
        )

        content = result["content"]
        assert len(content) == 1
        assert content[0]["type"] == "FILE"
        assert content[0]["contentAttachment"]["id"] == 77


# ---------------------------------------------------------------------------
# TaskSubmissionReviewService
# ---------------------------------------------------------------------------


class TestTaskSubmissionReviewService:
    def _build_service(
        self,
        review_repo=None,
        submission_repo=None,
        membership_repo=None,
        task_repo=None,
        rank_service=None,
    ):
        review_repo = review_repo or AsyncMock()
        return TaskSubmissionReviewService(
            review_repo=review_repo,
            submission_repo=submission_repo,
            membership_repo=membership_repo,
            task_repo=task_repo,
            rank_service=rank_service,
        )

    @pytest.mark.anyio
    async def test_get_review_dto_no_review(self):
        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(review_repo=review_repo)
        result = await svc.get_review_dto(submission_id=200)

        assert result == {"reviewed": False}

    @pytest.mark.anyio
    async def test_get_review_dto_with_review(self):
        review = _make_review(accepted=True, score=95, comment="Excellent")
        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = review

        svc = self._build_service(review_repo=review_repo)
        result = await svc.get_review_dto(submission_id=200)

        assert result["reviewed"] is True
        assert result["detail"]["accepted"] is True
        assert result["detail"]["score"] == 95
        assert result["detail"]["comment"] == "Excellent"

    @pytest.mark.anyio
    async def test_create_review_without_rank_service(self):
        review = _make_review()
        review_repo = AsyncMock()
        review_repo.create_review.return_value = review
        review_repo.get_by_submission_id.return_value = review

        svc = self._build_service(review_repo=review_repo, rank_service=None)
        result = await svc.create_review(
            submission_id=200,
            accepted=True,
            score=90,
            comment="Good",
        )

        assert result["reviewed"] is True
        assert result["hasUpgradedParticipantRank"] is False
        review_repo.create_review.assert_awaited_once()

    @pytest.mark.anyio
    async def test_create_review_with_rank_award(self):
        review = _make_review()
        submission = _make_submission(id=200, membership_id=50, submitter_id=42)
        membership = _make_membership(id=50, task_id=1)
        task = _make_task(id=1, space_id=100, rank=3)

        review_repo = AsyncMock()
        review_repo.create_review.return_value = review
        review_repo.get_by_submission_id.return_value = review

        submission_repo = AsyncMock()
        submission_repo.get_by_id.return_value = submission

        membership_repo = AsyncMock()
        membership_repo.get_by_id.return_value = membership

        task_repo = AsyncMock()
        task_repo.get_by_id.return_value = task

        rank_service = AsyncMock()
        rank_service.award_rank_if_higher.return_value = True

        svc = self._build_service(
            review_repo=review_repo,
            submission_repo=submission_repo,
            membership_repo=membership_repo,
            task_repo=task_repo,
            rank_service=rank_service,
        )

        result = await svc.create_review(
            submission_id=200,
            accepted=True,
            score=90,
            comment="Good",
        )

        assert result["hasUpgradedParticipantRank"] is True
        rank_service.award_rank_if_higher.assert_awaited_once_with(
            space_id=100,
            user_id=42,
            task_rank=3,
        )

    @pytest.mark.anyio
    async def test_patch_review_happy_path(self):
        review = _make_review(accepted=False, score=50, comment="Needs work")
        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = review
        review_repo.save.side_effect = lambda r: r

        svc = self._build_service(review_repo=review_repo)
        result = await svc.patch_review(
            submission_id=200,
            accepted=True,
            score=85,
        )

        assert review.accepted is True
        assert review.score == 85
        # comment was not passed, so it should remain unchanged
        assert review.comment == "Needs work"
        assert result["hasUpgradedParticipantRank"] is False

    @pytest.mark.anyio
    async def test_patch_review_not_found(self):
        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(review_repo=review_repo)

        with pytest.raises(NotFoundError):
            await svc.patch_review(submission_id=999)

    @pytest.mark.anyio
    async def test_delete_review_exists(self):
        review = _make_review()
        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = review

        svc = self._build_service(review_repo=review_repo)
        await svc.delete_review(submission_id=200)

        review_repo.soft_delete.assert_awaited_once_with(review)

    @pytest.mark.anyio
    async def test_delete_review_not_found_is_noop(self):
        review_repo = AsyncMock()
        review_repo.get_by_submission_id.return_value = None

        svc = self._build_service(review_repo=review_repo)
        await svc.delete_review(submission_id=999)

        review_repo.soft_delete.assert_not_awaited()

    @pytest.mark.anyio
    async def test_maybe_award_rank_skipped_when_not_accepted(self):
        """_maybe_award_rank returns False when new_accepted is False."""
        rank_service = AsyncMock()
        submission_repo = AsyncMock()

        svc = self._build_service(
            rank_service=rank_service,
            submission_repo=submission_repo,
        )
        result = await svc._maybe_award_rank(
            submission_id=200,
            previous_accepted=None,
            new_accepted=False,
        )

        assert result is False
        rank_service.award_rank_if_higher.assert_not_awaited()

    @pytest.mark.anyio
    async def test_maybe_award_rank_skipped_when_already_accepted(self):
        """No double-award: skip when previous_accepted was already True."""
        rank_service = AsyncMock()
        submission_repo = AsyncMock()

        svc = self._build_service(
            rank_service=rank_service,
            submission_repo=submission_repo,
        )
        result = await svc._maybe_award_rank(
            submission_id=200,
            previous_accepted=True,
            new_accepted=True,
        )

        assert result is False
        rank_service.award_rank_if_higher.assert_not_awaited()
