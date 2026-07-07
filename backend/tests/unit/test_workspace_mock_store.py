"""Unit tests for the Phase-A workspace mock store.

These guard the design invariants the mock exists to exercise: chat is
append-only, document nodes are editable in place, and a work item **locks** on
claim (not preemptible, content frozen, append-only annotations that notify the
owner). The store is process-local, so each test uses a fresh instance.
"""

import pytest

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.domain.workspace.mock_store import DEMO_PROJECT_ID, MockWorkspace
from app.domain.workspace.schemas import (
    DocNodeKind,
    ThreadKind,
    UserSummary,
    WorkItemStatus,
)

ALICE = UserSummary(id=1, username="alice", nickname="Alice")
BOB = UserSummary(id=9203, username="bob", nickname="Bob")


def _store() -> MockWorkspace:
    return MockWorkspace()


# ── 事项: the lock model ────────────────────────────────────────────────────────


def test_claim_locks_and_is_not_preemptible() -> None:
    s = _store()
    item = s.create_workitem(DEMO_PROJECT_ID, ALICE, "T", "d", None)
    assert item.owner is None and not item.locked

    claimed = s.claim_workitem(item.id, ALICE)
    assert claimed.owner is not None and claimed.owner.id == ALICE.id
    assert claimed.locked and claimed.status == WorkItemStatus.in_progress

    with pytest.raises(ConflictError):
        s.claim_workitem(item.id, BOB)  # another actor cannot preempt


def test_claim_is_idempotent_for_the_owner() -> None:
    s = _store()
    item = s.create_workitem(DEMO_PROJECT_ID, ALICE, "T", "d", None)
    s.claim_workitem(item.id, ALICE)
    again = s.claim_workitem(item.id, ALICE)  # no raise
    assert again.owner is not None and again.owner.id == ALICE.id


def test_only_owner_updates_status() -> None:
    s = _store()
    item = s.create_workitem(DEMO_PROJECT_ID, ALICE, "T", "d", None)
    s.claim_workitem(item.id, ALICE)
    with pytest.raises(ForbiddenError):
        s.update_status(item.id, BOB, WorkItemStatus.done)
    done = s.update_status(item.id, ALICE, WorkItemStatus.done)
    assert done.status == WorkItemStatus.done


def test_annotation_is_append_only_and_notifies_owner() -> None:
    s = _store()
    item = s.create_workitem(DEMO_PROJECT_ID, ALICE, "T", "d", None)
    s.claim_workitem(item.id, ALICE)
    s.notifications.clear()

    s.add_annotation(item.id, BOB, "progress note")  # a non-owner may append
    detail = s.get_workitem(item.id)
    assert detail.work_item.annotation_count == 1
    assert detail.annotations[-1].content == "progress note"
    # the owner (Alice) is notified; the author (Bob) is not
    assert any(uid == ALICE.id for uid, _ in s.notifications)


def test_owner_self_annotation_does_not_notify() -> None:
    s = _store()
    item = s.create_workitem(DEMO_PROJECT_ID, ALICE, "T", "d", None)
    s.claim_workitem(item.id, ALICE)
    s.notifications.clear()
    s.add_annotation(item.id, ALICE, "my own note")
    assert s.notifications == []


# ── 群聊: append-only chat ───────────────────────────────────────────────────────


def test_messages_are_appended_and_update_thread() -> None:
    s = _store()
    thread = s.create_thread(DEMO_PROJECT_ID, ALICE, "chat", ThreadKind.general, [BOB.id])
    m1 = s.post_message(thread.id, ALICE, "hello", None, [])
    m2 = s.post_message(thread.id, BOB, "hi", None, [])
    msgs, page = s.list_messages(thread.id, None, 30)
    assert [m.id for m in msgs] == [m1.id, m2.id]
    assert not page.has_more
    assert s.get_thread(thread.id).last_message is not None
    assert s.get_thread(thread.id).last_message.id == m2.id


def test_message_pagination() -> None:
    s = _store()
    thread = s.create_thread(DEMO_PROJECT_ID, ALICE, "chat", ThreadKind.general, [])
    ids = [s.post_message(thread.id, ALICE, str(i), None, []).id for i in range(5)]
    first, page = s.list_messages(thread.id, None, 2)
    assert [m.id for m in first] == ids[:2]
    assert page.has_more and page.next_start == ids[2]
    rest, page2 = s.list_messages(thread.id, page.next_start, 10)
    assert [m.id for m in rest] == ids[2:]
    assert not page2.has_more


# ── 文档: editable state ─────────────────────────────────────────────────────────


def test_document_node_is_editable_in_place() -> None:
    s = _store()
    doc = s.create_document(DEMO_PROJECT_ID, ALICE, "Doc", None)
    node = s.create_node(doc.id, ALICE, DocNodeKind.paragraph, "v1", None, None)
    edited = s.edit_node(doc.id, node.id, BOB, "v2")
    assert edited.id == node.id  # same node, mutated (not a new append)
    assert edited.content == "v2"
    assert edited.edited_by is not None and edited.edited_by.id == BOB.id
    detail = s.get_document(doc.id)
    assert len(detail.nodes) == 1 and detail.nodes[0].content == "v2"


def test_missing_entities_raise_not_found() -> None:
    s = _store()
    with pytest.raises(NotFoundError):
        s.get_thread(999999)
    with pytest.raises(NotFoundError):
        s.get_workitem(999999)
    with pytest.raises(NotFoundError):
        s.get_document(999999)


# ── seed sanity ─────────────────────────────────────────────────────────────────


def test_seed_has_a_locked_agent_owned_item() -> None:
    s = _store()
    items = s.list_workitems(DEMO_PROJECT_ID)
    locked = [w for w in items if w.locked and w.owner is not None]
    assert locked, "seed should include a locked, owned work item"
    assert any(w.owner is not None and w.owner.is_agent for w in locked)
