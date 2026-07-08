"""Unit tests for ``DocumentService`` (behavioural, no database).

The service builds its repositories from an ``AsyncSession`` in ``__init__``; we
construct it with a dummy session then swap the three collaborators for in-memory
fakes. This lets us assert *actual behaviour* — doc-root creation, node reconcile
(identical = no-op, edit-middle keeps neighbour ids), the edit EVENT, and the
membership 403 — without a DB.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import ForbiddenError
from app.domain.block.models import BlockKind
from app.domain.document.services import DocumentService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_NOW = datetime.now(UTC)


class FakeBlockRepo:
    def __init__(self) -> None:
        self.blocks: dict[int, SimpleNamespace] = {}
        self.events: list[SimpleNamespace] = []
        self._seq = 1000

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    async def add_doc_root(self, *, project_id, author_id, content):  # type: ignore[no-untyped-def]
        b = SimpleNamespace(
            id=self._next(),
            kind=BlockKind.DOC_ROOT,
            project_id=project_id,
            author_id=author_id,
            struct_parent_id=None,
            node_type=None,
            struct_order=None,
            content=content,
            edited_at=None,
        )
        self.blocks[b.id] = b
        return b

    async def add_doc_node(  # type: ignore[no-untyped-def]
        self, *, project_id, author_id, struct_parent_id, content, node_type, struct_order
    ):
        b = SimpleNamespace(
            id=self._next(),
            kind=BlockKind.DOC_NODE,
            project_id=project_id,
            author_id=author_id,
            struct_parent_id=struct_parent_id,
            node_type=node_type,
            struct_order=struct_order,
            content=content,
            edited_at=None,
        )
        self.blocks[b.id] = b
        return b

    async def add_event(self, *, project_id, author_id, content, refs=None, thread_id=None):  # type: ignore[no-untyped-def]
        e = SimpleNamespace(
            id=self._next(),
            project_id=project_id,
            author_id=author_id,
            content=content,
            refs=refs,
        )
        self.events.append(e)
        return e

    async def get(self, block_id):  # type: ignore[no-untyped-def]
        return self.blocks.get(block_id)

    async def list_doc_nodes(self, root_id):  # type: ignore[no-untyped-def]
        nodes = [
            b
            for b in self.blocks.values()
            if b.struct_parent_id == root_id and b.kind == BlockKind.DOC_NODE
        ]
        return sorted(nodes, key=lambda b: b.struct_order)

    async def update_content(self, block, content):  # type: ignore[no-untyped-def]
        block.content = content
        block.edited_at = _NOW
        return block

    async def update_node(self, block, *, node_type, struct_order):  # type: ignore[no-untyped-def]
        block.node_type = node_type
        block.struct_order = struct_order
        return block

    async def delete(self, block):  # type: ignore[no-untyped-def]
        self.blocks.pop(block.id, None)


class FakeDocRepo:
    def __init__(self) -> None:
        self.docs: dict[int, SimpleNamespace] = {}
        self._seq = 1

    async def create(  # type: ignore[no-untyped-def]
        self, *, project_id, parent_id, title, created_by, doc_root_block_id,
        sort_order=0.0, doc_type=0,
    ):
        self._seq += 1
        d = SimpleNamespace(
            id=self._seq,
            project_id=project_id,
            parent_id=parent_id,
            title=title,
            doc_type=int(doc_type),
            doc_root_block_id=doc_root_block_id,
            sort_order=sort_order,
            created_by=created_by,
            archived=False,
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.docs[d.id] = d
        return d

    async def get(self, document_id):  # type: ignore[no-untyped-def]
        return self.docs.get(document_id)

    async def list_for_project(self, project_id):  # type: ignore[no-untyped-def]
        return [d for d in self.docs.values() if d.project_id == project_id]

    async def children_of(self, project_id, parent_id):  # type: ignore[no-untyped-def]
        return [
            d
            for d in self.docs.values()
            if d.project_id == project_id and d.parent_id == parent_id
        ]

    async def next_sort_order(self, project_id, parent_id):  # type: ignore[no-untyped-def]
        sibs = await self.children_of(project_id, parent_id)
        return max((s.sort_order for s in sibs), default=-1.0) + 1.0

    async def update(  # type: ignore[no-untyped-def]
        self, doc, *, title=None, parent_id=None, set_parent=False,
        sort_order=None, archived=None,
    ):
        if title is not None:
            doc.title = title
        if set_parent:
            doc.parent_id = parent_id
        if sort_order is not None:
            doc.sort_order = sort_order
        if archived is not None:
            doc.archived = archived
        doc.updated_at = _NOW
        return doc

    async def touch(self, doc):  # type: ignore[no-untyped-def]
        doc.updated_at = _NOW
        return doc

    async def delete(self, doc):  # type: ignore[no-untyped-def]
        self.docs.pop(doc.id, None)


class FakeMembers:
    def __init__(self, members: set[tuple[int, int]]) -> None:
        self._members = members

    async def get_relation(self, project_id, user_id):  # type: ignore[no-untyped-def]
        if (project_id, user_id) in self._members:
            return SimpleNamespace(project_id=project_id, user_id=user_id, role=0)
        return None


def _make_service(members: set[tuple[int, int]]) -> DocumentService:
    svc = DocumentService.__new__(DocumentService)
    svc._session = None  # type: ignore[attr-defined]
    svc._docs = FakeDocRepo()  # type: ignore[attr-defined]
    svc._blocks = FakeBlockRepo()  # type: ignore[attr-defined]
    svc._members = FakeMembers(members)  # type: ignore[attr-defined]
    return svc


PROJECT = 7
ACTOR = 42


async def test_create_makes_doc_root_and_document() -> None:
    svc = _make_service({(PROJECT, ACTOR)})
    doc = await svc.create_document(PROJECT, ACTOR, "设计稿", None)

    assert doc.title == "设计稿"
    assert doc.project_id == PROJECT
    assert doc.created_by == ACTOR
    # 恰好创建了一个空的 DOC_ROOT 块。
    roots = [b for b in svc._blocks.blocks.values() if b.kind == BlockKind.DOC_ROOT]  # type: ignore[attr-defined]
    assert len(roots) == 1
    assert roots[0].content == ""


async def test_save_body_reconciles_and_appends_event() -> None:
    svc = _make_service({(PROJECT, ACTOR)})
    doc = await svc.create_document(PROJECT, ACTOR, "d", None)

    detail = await svc.save_body(doc.id, ACTOR, "A\n\nB\n\nC")
    assert [n.content for n in detail.nodes] == ["A", "B", "C"]
    first_ids = [n.id for n in detail.nodes]
    assert len(svc._blocks.events) == 1  # type: ignore[attr-defined]

    # 编辑中间节点:两侧邻居的 id 必须保留(锚点存活),中间被 delete+insert。
    detail2 = await svc.save_body(doc.id, ACTOR, "A\n\nX\n\nC")
    assert [n.content for n in detail2.nodes] == ["A", "X", "C"]
    ids2 = [n.id for n in detail2.nodes]
    assert ids2[0] == first_ids[0]  # A 复用
    assert ids2[2] == first_ids[2]  # C 复用
    assert ids2[1] != first_ids[1]  # 中间是新块
    assert len(svc._blocks.events) == 2  # type: ignore[attr-defined]


async def test_save_identical_markdown_is_noop() -> None:
    svc = _make_service({(PROJECT, ACTOR)})
    doc = await svc.create_document(PROJECT, ACTOR, "d", None)
    await svc.save_body(doc.id, ACTOR, "A\n\nB")
    blocks_after_first = dict(svc._blocks.blocks)  # type: ignore[attr-defined]
    events_after_first = len(svc._blocks.events)  # type: ignore[attr-defined]

    await svc.save_body(doc.id, ACTOR, "A\n\nB")
    # 完全相同的 markdown:不新增/删除块,不追加事件。
    assert svc._blocks.blocks == blocks_after_first  # type: ignore[attr-defined]
    assert len(svc._blocks.events) == events_after_first  # type: ignore[attr-defined]


async def test_non_member_is_rejected() -> None:
    svc = _make_service(set())  # nobody is a member
    with pytest.raises(ForbiddenError):
        await svc.create_document(PROJECT, ACTOR, "d", None)

    # 读操作同样拒绝非成员。
    svc2 = _make_service({(PROJECT, ACTOR)})
    doc = await svc2.create_document(PROJECT, ACTOR, "d", None)
    svc2._members = FakeMembers(set())  # type: ignore[attr-defined]
    with pytest.raises(ForbiddenError):
        await svc2.get_document(doc.id, 999)
