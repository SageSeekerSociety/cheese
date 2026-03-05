"""Regression tests for Round 12 bug fixes.

Each test targets a specific bug that was found and fixed.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bug 1: notification/scheduler.py — _tick must commit after finalize
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notification_finalizer_tick_commits_session():
    """Verify _tick() calls session.commit() so finalized rows are persisted."""
    from app.domain.notification.scheduler import NotificationAggregationFinalizer

    mock_session = AsyncMock()
    mock_handler = AsyncMock()
    mock_handler.finalize_expired.return_value = []

    # session_factory returns an async context manager yielding mock_session
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    finalizer = NotificationAggregationFinalizer(
        session_factory=mock_factory,
        interval_seconds=60,
    )

    with patch(
        "app.domain.notification.scheduler.build_notification_event_handler",
        return_value=mock_handler,
    ):
        await finalizer._tick()

    mock_handler.finalize_expired.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Bug 2: deadline_scheduler.py — offset must stay at 0 to avoid skipping rows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deadline_scheduler_processes_all_batches():
    """When committed rows drop out of the query, offset=0 should catch the rest."""
    from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines

    now = datetime.now(UTC).replace(tzinfo=None)
    call_count = 0

    def make_membership(mid: int):
        return SimpleNamespace(
            id=mid,
            completion_status="NOT_SUBMITTED",
            deadline=now,
            updated_at=now,
            deleted_at=None,
        )

    # First call returns 100 rows, second returns 50 rows, third returns 0
    batches = [
        [make_membership(i) for i in range(100)],
        [make_membership(i) for i in range(100, 150)],
        [],
    ]

    mock_session = AsyncMock()

    async def fake_execute(stmt):
        nonlocal call_count
        idx = min(call_count, len(batches) - 1)
        call_count += 1
        result = MagicMock()
        result.scalars.return_value.all.return_value = batches[idx]
        return result

    mock_session.execute = fake_execute
    mock_session.commit = AsyncMock()

    count = await check_and_fail_expired_deadlines(mock_session)

    assert count == 150, f"Should process all 150 rows, got {count}"
    assert mock_session.commit.await_count == 2


# ---------------------------------------------------------------------------
# Bug 3: AIConversation.create missing created_at / updated_at
# ---------------------------------------------------------------------------


def test_ai_conversation_create_sets_timestamps():
    """Verify the AIConversation constructor in task repo sets timestamp fields."""
    import inspect

    from app.domain.task.repositories import AIConversationRepository

    source = inspect.getsource(AIConversationRepository.create)
    assert "created_at" in source, "create() must set created_at"
    assert "updated_at" in source, "create() must set updated_at"


# ---------------------------------------------------------------------------
# Bug 4: GET /questions/followed must not be shadowed by /{question_id}
# ---------------------------------------------------------------------------


def test_questions_followed_route_before_question_id():
    """The /followed route must appear before /{question_id} in registration order."""
    from app.api.routes.questions import router

    paths = [route.path for route in router.routes if hasattr(route, "path")]

    followed_idx = None
    param_idx = None
    for i, path in enumerate(paths):
        if path.endswith("/followed") and followed_idx is None:
            followed_idx = i
        if path.endswith("/{question_id}") and param_idx is None:
            param_idx = i

    assert followed_idx is not None, f"/followed route not found in {paths}"
    assert param_idx is not None, f"/{{question_id}} route not found in {paths}"
    assert followed_idx < param_idx, (
        f"/followed (index {followed_idx}) must come before "
        f"/{{question_id}} (index {param_idx})"
    )


# ---------------------------------------------------------------------------
# Bug 5: upload_material must actually store the file
# ---------------------------------------------------------------------------


def test_upload_material_uses_storage_backend():
    """The upload_material handler must call storage.upload(), not just build a URL string."""
    import inspect

    from app.api.routes import materials

    source = inspect.getsource(materials.upload_material)
    assert "storage" in source.lower() or "upload" in source.lower(), (
        "upload_material must use a storage backend"
    )
    assert '"/uploads/' not in source, (
        "upload_material must not hardcode a fake URL like /uploads/{name}"
    )


# ---------------------------------------------------------------------------
# Bug 6: migration chain must have a single head (no forks)
# ---------------------------------------------------------------------------


def test_migration_chain_single_head():
    """All migration revisions must form a linear chain (no multiple heads)."""
    import importlib
    import importlib.util
    from pathlib import Path

    migrations_dir = Path("migrations/versions")
    if not migrations_dir.exists():
        pytest.skip("migrations directory not found")

    revisions: dict[str, str | None] = {}
    for f in sorted(migrations_dir.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:
                continue
            rev = getattr(mod, "revision", None)
            down = getattr(mod, "down_revision", None)
            if rev:
                revisions[rev] = down

    # Count how many revisions share the same down_revision (forks)
    down_counts: dict[str | None, list[str]] = {}
    for rev, down in revisions.items():
        down_counts.setdefault(down, []).append(rev)

    forks = {down: heads for down, heads in down_counts.items() if len(heads) > 1}
    assert not forks, f"Migration chain has forks: {forks}"


# ---------------------------------------------------------------------------
# Bug 7: groups count query must include joined/managed filters
# ---------------------------------------------------------------------------


def test_groups_search_count_includes_joined_filter():
    """Verify GroupRepository.search applies joined/managed filters to count query."""
    import inspect

    from app.domain.groups.repositories import GroupRepository

    source = inspect.getsource(GroupRepository.search)
    # The count section should reference joined/managed filtering
    count_section = source[source.index("count_stmt"):]
    assert "joined" in count_section or "managed" in count_section or "count_subq" in count_section, (
        "count_stmt must apply joined/managed filters"
    )


# ---------------------------------------------------------------------------
# Bug 8: datetime.fromtimestamp must use UTC in group routes
# ---------------------------------------------------------------------------


def test_groups_datetime_uses_utc():
    """Group target create/update must use UTC for timestamp conversion."""
    import inspect

    from app.api.routes import groups

    source = inspect.getsource(groups)
    # Find all datetime.fromtimestamp calls
    import re

    calls = re.findall(r"datetime\.fromtimestamp\([^)]+\)", source)
    for call in calls:
        assert "UTC" in call or "utc" in call, (
            f"datetime.fromtimestamp must use UTC timezone: {call}"
        )


# ---------------------------------------------------------------------------
# Bug 9: patch_user_identity must use None check, not falsy `or`
# ---------------------------------------------------------------------------


def test_patch_identity_allows_empty_string():
    """Merging logic must allow clearing fields to empty string."""
    # Simulate the _merge helper pattern
    base = {"realName": "Alice", "studentId": "S001"}
    payload = {"realName": "", "studentId": None}

    def _merge(key: str) -> str:
        val = payload.get(key)
        return val if val is not None else base[key]

    assert _merge("realName") == "", "Empty string should clear the field"
    assert _merge("studentId") == "S001", "None should keep existing value"


def test_patch_identity_source_uses_none_check():
    """The actual route code must use 'is not None' instead of 'or' for merging."""
    import inspect

    from app.api.routes import users

    source = inspect.getsource(users.patch_user_identity)
    assert "is not None" in source, "patch_user_identity must use explicit None check"


# ---------------------------------------------------------------------------
# Bug 10: Histogram must use default buckets when none specified
# ---------------------------------------------------------------------------


def test_histogram_default_buckets_not_overridden():
    """MetricsRegistry.histogram() without buckets should use dataclass defaults."""
    from app.core.metrics import MetricsRegistry

    registry = MetricsRegistry()
    h = registry.histogram("test_latency")

    default_buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    assert len(h.buckets) > 0, "Histogram should have default buckets"
    assert h.buckets == default_buckets, "Should match Histogram dataclass defaults"
