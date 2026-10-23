"""Both membership stores remove rows, flush, and leave no soft-delete marker."""

from unittest.mock import AsyncMock, Mock, PropertyMock, call, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectMember
from app.domain.topic.models import TopicMembership
from app.domain.topic_membership.repositories import TopicMembershipRepository


async def test_membership_deletes_follow_one_hard_delete_rule():
    completed = []

    async def delete(row):
        completed.append(("delete", row))

    async def flush():
        completed.append(("flush",))

    session = Mock(spec=AsyncSession)
    session.delete = AsyncMock(side_effect=delete)
    session.flush = AsyncMock(side_effect=flush)
    traces = []

    for repository, model in (
        (MemberRepository, ProjectMember),
        (TopicMembershipRepository, TopicMembership),
    ):
        member = model()
        session.reset_mock()
        completed.clear()
        # A write of None (or a write later undone) is still a soft-delete write.
        with patch.object(
            model, "deleted_at", new_callable=PropertyMock, create=True
        ) as deleted_at:
            deleted_at.return_value = None
            result = await repository(session).delete(member)

        assert result is None
        assert session.mock_calls == [call.delete(member), call.flush()]
        # Side effects run on await, not merely when the coroutine is created.
        assert completed == [("delete", member), ("flush",)]
        session.delete.assert_awaited_once_with(member)
        session.flush.assert_awaited_once_with()
        assert not any(event.args for event in deleted_at.mock_calls)
        traces.append([event[0] for event in completed])

    assert traces == [["delete", "flush"], ["delete", "flush"]]
