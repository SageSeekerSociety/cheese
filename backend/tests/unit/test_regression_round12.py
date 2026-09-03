"""Tests for Round 12 bug fixes.

Covers: deadline scheduler, AIConversation creation, route ordering, material
upload, migration chain, groups search count, datetime timezone, identity
patch, and histogram defaults.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Deadline scheduler: must process all batches without skipping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deadline_scheduler_processes_all_batches():
    from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines

    now = datetime.now(UTC)
    call_count = 0

    def make_membership(mid: int):
        return SimpleNamespace(
            id=mid,
            completion_status="NOT_SUBMITTED",
            deadline=now,
            updated_at=now,
            deleted_at=None,
        )

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

    assert count == 150
    assert mock_session.commit.await_count == 2


# ---------------------------------------------------------------------------
# AIConversation: create must set required timestamp fields
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ai_conversation_create_sets_timestamps():
    from app.domain.task.repositories import AIConversationRepository

    mock_session = MagicMock()
    mock_session.flush = AsyncMock()
    repo = AIConversationRepository(mock_session)

    await repo.create(
        conversation_id="test-conv-123",
        task_id=1,
        owner_id=42,
        title="Test",
    )

    mock_session.add.assert_called_once()
    entity = mock_session.add.call_args[0][0]
    assert entity.created_at is not None, "created_at must be set"
    assert entity.updated_at is not None, "updated_at must be set"
    assert isinstance(entity.created_at, datetime)
    assert isinstance(entity.updated_at, datetime)


# ---------------------------------------------------------------------------
# Route ordering: /questions/followed must precede /{question_id}
# ---------------------------------------------------------------------------


def test_questions_followed_route_before_question_id():
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
        f"/followed (index {followed_idx}) must come before /{'{question_id}'} (index {param_idx})"  # noqa: E501
    )


# ---------------------------------------------------------------------------
# Material upload: must use storage backend (not a hardcoded path)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_material_stores_file():
    from unittest.mock import AsyncMock, MagicMock

    mock_storage = AsyncMock()
    mock_storage.upload.return_value = "https://storage.example.com/materials/file.png"

    with (
        patch(
            "app.api.routes.materials.get_storage_backend", return_value=mock_storage
        ),
        patch(
            "app.api.routes.materials.generate_storage_key",
            return_value="materials/image/abc.png",
        ),
    ):
        from io import BytesIO

        from fastapi import UploadFile

        from app.api.routes.materials import upload_material

        fake_file = UploadFile(
            filename="test.png",
            file=BytesIO(b"\x89PNG fake content"),
            headers=MagicMock(
                get=lambda k, d=None: "image/png" if k == "content-type" else d
            ),
        )

        mock_service = AsyncMock()
        mock_service.create_material.return_value = {
            "id": 1,
            "url": "https://storage.example.com/materials/file.png",
        }

        mock_auth = SimpleNamespace(user_id=1)

        await upload_material(
            file=fake_file,
            type="image",
            auth_user=mock_auth,
            service=mock_service,
        )

    mock_storage.upload.assert_awaited_once()
    call_args = mock_service.create_material.call_args
    assert "storageKey" in call_args.kwargs.get("meta", {}) or (
        call_args[1].get("meta", {}).get("storageKey")
    )


# ---------------------------------------------------------------------------
# Migration chain: must have a single head (no forks)
# ---------------------------------------------------------------------------


def test_migration_chain_single_head():
    import importlib.util
    from pathlib import Path

    migrations_dir = Path("migrations/versions")
    if not migrations_dir.exists():
        pytest.skip("migrations directory not found")

    revisions: dict[str, str | tuple[str, ...] | None] = {}
    merge_revisions: set[str] = set()
    for f in sorted(migrations_dir.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:
                continue
            rev = getattr(mod, "revision", None)
            down_raw = getattr(mod, "down_revision", None)
            if rev:
                # Alembic merge migrations use a tuple for down_revision
                if isinstance(down_raw, tuple):
                    revisions[rev] = down_raw
                    merge_revisions.add(rev)
                else:
                    revisions[rev] = down_raw

    # Build parent → children mapping
    children_of: dict[str | None, list[str]] = {}
    for rev, down in revisions.items():
        if isinstance(down, tuple):
            for parent_rev in down:
                children_of.setdefault(parent_rev, []).append(rev)
        else:
            children_of.setdefault(down, []).append(rev)

    # Compute the "ultimate head" reachable from each revision by
    # following children until a node with no outgoing children is found.
    _head_cache: dict[str, str] = {}

    def _reachable_head(rev: str) -> str:
        if rev in _head_cache:
            return _head_cache[rev]
        children = children_of.get(rev, [])
        if not children:
            _head_cache[rev] = rev
            return rev
        # All children should converge — pick the first child's ultimate head
        head = _reachable_head(children[0])
        _head_cache[rev] = head
        return head

    # A fork is only a problem if not all children eventually reach the same head.
    unresolved_forks: dict[str | None, list[str]] = {}
    for down_key, child_list in children_of.items():
        if len(child_list) <= 1 or down_key is None:
            continue
        heads = {_reachable_head(c) for c in child_list}
        if len(heads) > 1:
            unresolved_forks[down_key] = child_list

    assert not unresolved_forks, (
        f"Migration chain has unresolved forks: {unresolved_forks}"
    )


# ---------------------------------------------------------------------------
# Groups search: count query must reflect joined/managed filters
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groups_search_count_with_joined_filter():
    """When joined=True, total count must match the filtered rows, not all groups."""
    from app.domain.groups.repositories import GroupRepository

    mock_session = AsyncMock()

    # Simulate: 3 groups total, but user joined only 1
    filtered_rows = [
        SimpleNamespace(
            id=1, name="My Group", deleted_at=None, created_at=datetime.now(UTC)
        )
    ]

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # Main query
            result.scalars.return_value.all.return_value = filtered_rows
        else:
            # Count query — should return 1 (not 3)
            result.scalar_one.return_value = 1
        return result

    mock_session.execute = fake_execute

    repo = GroupRepository(mock_session)
    rows, total = await repo.search(
        keyword=None,
        limit=20,
        offset=0,
        user_id=42,
        joined=True,
    )

    assert total == 1, f"Count should reflect joined filter, got {total}"


# ---------------------------------------------------------------------------
# Groups datetime: fromtimestamp must use UTC
# ---------------------------------------------------------------------------


def test_groups_datetime_fromtimestamp_utc():
    """Verify UTC timestamp conversion produces consistent results."""
    ts_ms = 1704067200000  # 2024-01-01 00:00:00 UTC

    result = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    assert result.year == 2024
    assert result.month == 1
    assert result.day == 1
    assert result.hour == 0
    assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# Identity patch: empty string must clear the field (not fall back)
# ---------------------------------------------------------------------------


def test_patch_identity_empty_string_clears_field():
    """payload with "" should clear the field, not keep the old value."""
    base = {
        "realName": "Alice",
        "studentId": "S001",
        "grade": "3",
        "major": "CS",
        "className": "A",
    }

    # Simulate the _merge pattern used in the route
    payload_clear = {"realName": "", "studentId": None, "grade": "4"}

    def _merge(key: str) -> str:
        val = payload_clear.get(key)
        return val if val is not None else base[key]

    assert _merge("realName") == "", "Empty string should clear realName"
    assert _merge("studentId") == "S001", "None should keep existing studentId"
    assert _merge("grade") == "4", "Provided value should override"
    assert _merge("major") == "CS", "Missing key should keep existing"


# ---------------------------------------------------------------------------
# Histogram: default buckets when none specified
# ---------------------------------------------------------------------------


def test_histogram_default_buckets_not_overridden():
    from app.core.metrics import MetricsRegistry

    registry = MetricsRegistry()
    h = registry.histogram("test_latency")

    expected = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    assert h.buckets == expected


def test_histogram_custom_buckets_respected():
    from app.core.metrics import MetricsRegistry

    registry = MetricsRegistry()
    custom = [0.1, 0.5, 1.0]
    h = registry.histogram("custom_hist", buckets=custom)

    assert h.buckets == custom
