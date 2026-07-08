"""Unit tests for 导出到文档 (export chat messages → project document).

No DB: fake BlockRepository / ThreadMembershipRepository (SimpleNamespace + AsyncMock)
and a DocumentService instantiated without a session, with its create/save/get methods
mocked. Functional — asserts observable behavior (rendered markdown, call sequencing),
never inspects source.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes.message_export import (
    ExportToDocumentBody,
    build_message_export_router,
    export_to_document,
)
from app.core.errors import ForbiddenError
from app.domain.block.models import BlockKind
from app.domain.document.schemas import DocumentDetail, DocumentSummary
from app.domain.document.services import DocumentService, ExportMessage

pytestmark = pytest.mark.anyio


def _msg_block(block_id: int, author_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=block_id,
        author_id=author_id,
        content=text,
        kind=BlockKind.MESSAGE,
        created_at=datetime(2026, 7, 7, 12, 0, block_id, tzinfo=UTC),
    )


def _fake_block_repo(blocks: dict[int, SimpleNamespace]) -> SimpleNamespace:
    async def get_message_in_thread(
        thread_id: int, block_id: int
    ) -> SimpleNamespace | None:
        return blocks.get(block_id)

    return SimpleNamespace(get_message_in_thread=get_message_in_thread)


def _fake_membership(is_member: bool) -> SimpleNamespace:
    return SimpleNamespace(is_member=AsyncMock(return_value=is_member))


def _summary(doc_id: int = 42) -> DocumentSummary:
    return DocumentSummary(
        id=doc_id,
        project_id=7,
        parent_id=None,
        title="聊天记录导出",
        doc_type="markdown",
        sort_order=0.0,
        archived=False,
        created_by=1,
        created_at=0,
        updated_at=0,
    )


async def test_non_thread_member_is_rejected() -> None:
    body = ExportToDocumentBody(project_id=7, block_ids=[1, 2])
    doc_service = SimpleNamespace(export_messages=AsyncMock())
    with pytest.raises(ForbiddenError):
        await export_to_document(
            thread_id=99,
            body=body,
            user_id=5,
            block_repo=_fake_block_repo({}),
            membership_repo=_fake_membership(False),
            doc_service=doc_service,
        )
    doc_service.export_messages.assert_not_awaited()


async def test_export_passes_ordered_messages_with_text() -> None:
    blocks = {
        3: _msg_block(3, 100, "third"),
        1: _msg_block(1, 100, "first"),
        2: _msg_block(2, 200, "second"),
    }
    body = ExportToDocumentBody(project_id=7, block_ids=[3, 1, 2])
    doc_service = SimpleNamespace(export_messages=AsyncMock(return_value=_summary()))

    result = await export_to_document(
        thread_id=99,
        body=body,
        user_id=5,
        block_repo=_fake_block_repo(blocks),
        membership_repo=_fake_membership(True),
        doc_service=doc_service,
    )

    assert result["code"] == 200
    assert result["data"] == {"document": _summary().model_dump(by_alias=True)}
    kwargs = doc_service.export_messages.await_args.kwargs
    messages: list[ExportMessage] = kwargs["messages"]
    # sorted by id ascending → chronological
    assert [m.text for m in messages] == ["first", "second", "third"]
    assert kwargs["project_id"] == 7
    assert kwargs["actor_id"] == 5
    assert kwargs["document_id"] is None


async def test_create_mode_saves_rendered_markdown() -> None:
    service = DocumentService.__new__(DocumentService)
    service.create_document = AsyncMock(return_value=_summary(doc_id=42))  # type: ignore[method-assign]
    service.save_body = AsyncMock()  # type: ignore[method-assign]

    messages = [
        ExportMessage(author_name="用户 100", text="hello"),
        ExportMessage(author_name="用户 200", text="world"),
    ]
    summary = await service.export_messages(
        project_id=7, actor_id=5, messages=messages, title="My Export"
    )

    assert summary.id == 42
    service.create_document.assert_awaited_once_with(7, 5, "My Export")
    saved_content = service.save_body.await_args.args[2]
    assert "hello" in saved_content
    assert "world" in saved_content
    assert saved_content.index("hello") < saved_content.index("world")


async def test_append_mode_appends_to_existing_content() -> None:
    service = DocumentService.__new__(DocumentService)
    existing = DocumentDetail(document=_summary(doc_id=42), content="EXISTING BODY", nodes=[])
    service.get_document = AsyncMock(return_value=existing)  # type: ignore[method-assign]
    service.save_body = AsyncMock(  # type: ignore[method-assign]
        return_value=DocumentDetail(document=_summary(doc_id=42), content="", nodes=[])
    )

    messages = [ExportMessage(author_name="用户 100", text="appended-text")]
    await service.export_messages(
        project_id=7, actor_id=5, messages=messages, document_id=42
    )

    service.get_document.assert_awaited_once_with(42, 5)
    saved_content = service.save_body.await_args.args[2]
    assert saved_content.startswith("EXISTING BODY")
    assert "appended-text" in saved_content


def test_router_is_importable_and_mounts_route() -> None:
    router = build_message_export_router()
    paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
    assert "/connector/threads/{threadId}/export-to-document" in paths
