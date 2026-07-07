"""In-memory mock backing for the Phase-A workspace contract.

This is a throwaway scaffold: it lets the frontend and the `cheese` CLI develop
against a *real, consistent* API (POST then GET reflects the write) before the
real `block` / `thread` / `document` / `workitem` domains exist. Phase B/C delete
this module and implement the identical route/schema contract over SQLAlchemy.

It enforces the parts of the model that shape the contract — chat is append-only,
document nodes are editable, and a work item **locks** on claim (immutable content,
append-only annotations that notify the owner) — so those behaviours are exercised
end-to-end from day one. It is process-local and unpersisted by design.
"""

from datetime import UTC, datetime

from app.core.errors import ConflictError, ForbiddenError, NotFoundError

from .schemas import (
    AttentionPolicy,
    BlockRef,
    DocNode,
    DocNodeKind,
    Document,
    DocumentDetail,
    Message,
    MessageKind,
    Page,
    RefTargetType,
    Thread,
    ThreadKind,
    ThreadMember,
    UserSummary,
    WorkItem,
    WorkItemAnnotation,
    WorkItemDetail,
    WorkItemStatus,
)

# The reserved agent users (seeded by scripts/seed_workspace.py). In Phase D this
# is derived from the presence of an execution binding, not a hardcoded set.
AGENT_USER_IDS = {9204, 9205}

DEMO_PROJECT_ID = 1

# Fixed seed clock (2025-07-06T00:00:00Z) so the demo is deterministic.
_T0 = 1751760000000


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


class MockWorkspace:
    def __init__(self) -> None:
        self.users: dict[int, UserSummary] = {}
        self.threads: dict[int, Thread] = {}
        self.thread_members: dict[int, list[ThreadMember]] = {}
        self.messages: dict[int, list[Message]] = {}  # thread_id -> messages
        self.documents: dict[int, Document] = {}
        self.doc_nodes: dict[int, list[DocNode]] = {}  # document_id -> nodes
        self.workitems: dict[int, WorkItem] = {}
        self.annotations: dict[int, list[WorkItemAnnotation]] = {}  # workitem_id -> anns
        self.notifications: list[tuple[int, str]] = []  # (user_id, text) — mock delivery
        self._seq = 1000
        self._seed()

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    # ── user resolution ────────────────────────────────────────────────────────

    def remember_user(self, summary: UserSummary) -> UserSummary:
        """Cache an actor resolved from the real user table at the trust boundary."""
        existing = self.users.get(summary.id)
        if existing is not None:
            # keep agent hints (mock-derived), refresh identity fields
            summary.is_agent = existing.is_agent
            summary.agent_status = existing.agent_status
        self.users[summary.id] = summary
        return summary

    def user(self, user_id: int) -> UserSummary:
        u = self.users.get(user_id)
        if u is None:
            u = UserSummary(id=user_id, username=f"user{user_id}", nickname=f"用户{user_id}")
            self.users[user_id] = u
        return u

    # ── 群聊 ─────────────────────────────────────────────────────────────────────

    def list_threads(self, project_id: int) -> list[Thread]:
        out = [t for t in self.threads.values() if t.project_id == project_id]
        return sorted(out, key=lambda t: t.updated_at, reverse=True)

    def get_thread(self, thread_id: int) -> Thread:
        t = self.threads.get(thread_id)
        if t is None:
            raise NotFoundError("Thread not found")
        return t

    def create_thread(
        self, project_id: int, actor: UserSummary, title: str, kind: ThreadKind, member_ids: list[int]
    ) -> Thread:
        tid = self._next()
        members = [actor] + [self.user(uid) for uid in member_ids if uid != actor.id]
        self.thread_members[tid] = [ThreadMember(user=m) for m in members]
        self.messages[tid] = []
        t = Thread(
            id=tid,
            project_id=project_id,
            kind=kind,
            title=title,
            member_count=len(members),
            members=members[:5],
            updated_at=_now_ms(),
        )
        self.threads[tid] = t
        return t

    def list_messages(
        self, thread_id: int, page_start: int | None, page_size: int
    ) -> tuple[list[Message], Page]:
        self.get_thread(thread_id)
        msgs = self.messages.get(thread_id, [])
        start_idx = 0
        if page_start is not None:
            start_idx = next((i for i, m in enumerate(msgs) if m.id >= page_start), len(msgs))
        window = msgs[start_idx : start_idx + page_size]
        has_more = start_idx + page_size < len(msgs)
        next_start = msgs[start_idx + page_size].id if has_more else None
        return window, Page(
            page_start=page_start, page_size=page_size, has_more=has_more, next_start=next_start
        )

    def post_message(
        self,
        thread_id: int,
        actor: UserSummary,
        content: str,
        reply_to_id: int | None,
        refs: list[BlockRef],
    ) -> Message:
        t = self.get_thread(thread_id)
        m = Message(
            id=self._next(),
            thread_id=thread_id,
            author=actor,
            reply_to_id=reply_to_id,
            content=content,
            refs=refs,
            created_at=_now_ms(),
        )
        self.messages.setdefault(thread_id, []).append(m)
        t.last_message = m
        t.updated_at = m.created_at
        return m

    def list_members(self, thread_id: int) -> list[ThreadMember]:
        self.get_thread(thread_id)
        return self.thread_members.get(thread_id, [])

    def add_member(
        self, thread_id: int, user: UserSummary, role: str, override: AttentionPolicy | None
    ) -> ThreadMember:
        t = self.get_thread(thread_id)
        members = self.thread_members.setdefault(thread_id, [])
        if any(m.user.id == user.id for m in members):
            raise ConflictError("Already a member")
        member = ThreadMember(user=user, role=role, attention_policy_override=override)
        members.append(member)
        t.member_count = len(members)
        t.members = [m.user for m in members[:5]]
        return member

    def remove_member(self, thread_id: int, user_id: int) -> None:
        t = self.get_thread(thread_id)
        members = self.thread_members.get(thread_id, [])
        members[:] = [m for m in members if m.user.id != user_id]
        t.member_count = len(members)
        t.members = [m.user for m in members[:5]]

    # ── 文档 ─────────────────────────────────────────────────────────────────────

    def list_documents(self, project_id: int) -> list[Document]:
        return [d for d in self.documents.values() if d.project_id == project_id]

    def get_document(self, document_id: int) -> DocumentDetail:
        d = self.documents.get(document_id)
        if d is None:
            raise NotFoundError("Document not found")
        nodes = sorted(self.doc_nodes.get(document_id, []), key=lambda n: n.order)
        return DocumentDetail(document=d, nodes=nodes)

    def create_document(
        self, project_id: int, actor: UserSummary, title: str, parent_id: int | None
    ) -> Document:
        did = self._next()
        d = Document(
            id=did,
            project_id=project_id,
            parent_id=parent_id,
            title=title,
            updated_at=_now_ms(),
            updated_by=actor,
        )
        self.documents[did] = d
        self.doc_nodes[did] = []
        return d

    def create_node(
        self,
        document_id: int,
        actor: UserSummary,
        kind: DocNodeKind,
        content: str,
        struct_parent_id: int | None,
        order: int | None,
    ) -> DocNode:
        d = self.documents.get(document_id)
        if d is None:
            raise NotFoundError("Document not found")
        nodes = self.doc_nodes.setdefault(document_id, [])
        ts = _now_ms()
        node = DocNode(
            id=self._next(),
            document_id=document_id,
            struct_parent_id=struct_parent_id,
            kind=kind,
            content=content,
            order=order if order is not None else len(nodes),
            edited_at=ts,
            edited_by=actor,
        )
        nodes.append(node)
        d.updated_at = ts
        d.updated_by = actor
        return node

    def edit_node(
        self, document_id: int, node_id: int, actor: UserSummary, content: str
    ) -> DocNode:
        d = self.documents.get(document_id)
        if d is None:
            raise NotFoundError("Document not found")
        for n in self.doc_nodes.get(document_id, []):
            if n.id == node_id:
                ts = _now_ms()
                n.content = content  # documents are editable state
                n.edited_at = ts
                n.edited_by = actor
                d.updated_at = ts
                d.updated_by = actor
                return n
        raise NotFoundError("Node not found")

    def delete_node(self, document_id: int, node_id: int) -> None:
        nodes = self.doc_nodes.get(document_id)
        if nodes is None:
            raise NotFoundError("Document not found")
        nodes[:] = [n for n in nodes if n.id != node_id]

    # ── 事项 ─────────────────────────────────────────────────────────────────────

    def list_workitems(self, project_id: int) -> list[WorkItem]:
        return [w for w in self.workitems.values() if w.project_id == project_id]

    def get_workitem(self, work_item_id: int) -> WorkItemDetail:
        w = self.workitems.get(work_item_id)
        if w is None:
            raise NotFoundError("Work item not found")
        anns = self.annotations.get(work_item_id, [])
        return WorkItemDetail(work_item=w, annotations=anns)

    def create_workitem(
        self,
        project_id: int,
        actor: UserSummary,
        title: str,
        description: str,
        parent_id: int | None,
    ) -> WorkItem:
        wid = self._next()
        _ = actor  # creator is not the owner; ownership is taken via claim
        now = _now_ms()
        w = WorkItem(
            id=wid,
            project_id=project_id,
            parent_id=parent_id,
            title=title,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self.workitems[wid] = w
        self.annotations[wid] = []
        return w

    def claim_workitem(self, work_item_id: int, actor: UserSummary) -> WorkItem:
        """Taking ownership LOCKS the item: not preemptible, content frozen."""
        w = self.workitems.get(work_item_id)
        if w is None:
            raise NotFoundError("Work item not found")
        if w.owner is not None:
            if w.owner.id == actor.id:
                return w  # idempotent
            raise ConflictError(f"Already owned by {w.owner.nickname}")
        w.owner = actor
        w.locked = True
        w.status = WorkItemStatus.in_progress
        w.updated_at = _now_ms()
        return w

    def update_status(
        self, work_item_id: int, actor: UserSummary, status: WorkItemStatus
    ) -> WorkItem:
        w = self.workitems.get(work_item_id)
        if w is None:
            raise NotFoundError("Work item not found")
        if w.owner is None or w.owner.id != actor.id:
            raise ForbiddenError("Only the owner can change status")
        w.status = status
        w.updated_at = _now_ms()
        return w

    def add_annotation(
        self, work_item_id: int, actor: UserSummary, content: str
    ) -> WorkItemAnnotation:
        """The only mutation of a locked item — append-only, notifies the owner."""
        w = self.workitems.get(work_item_id)
        if w is None:
            raise NotFoundError("Work item not found")
        ann = WorkItemAnnotation(
            id=self._next(),
            work_item_id=work_item_id,
            author=actor,
            content=content,
            created_at=_now_ms(),
        )
        self.annotations.setdefault(work_item_id, []).append(ann)
        w.annotation_count = len(self.annotations[work_item_id])
        w.updated_at = ann.created_at
        if w.owner is not None and w.owner.id != actor.id:
            self.notifications.append(
                (w.owner.id, f"事项《{w.title}》有新注记：{content[:40]}")
            )
        return ann

    # ── seed ─────────────────────────────────────────────────────────────────────

    def _seed(self) -> None:
        alice = UserSummary(id=1, username="alice", nickname="Alice")
        bob = UserSummary(id=9203, username="bob", nickname="Bob")
        carol = UserSummary(id=3, username="carol", nickname="Carol")
        main = UserSummary(
            id=9204, username="cheese-main", nickname="芝士·主管",
            is_agent=True, agent_status=None,
        )
        data = UserSummary(
            id=9205, username="cheese-data", nickname="芝士·数据",
            is_agent=True, agent_status=None,
        )
        for u in (alice, bob, carol, main, data):
            self.users[u.id] = u
        p = DEMO_PROJECT_ID

        # workitems (事项树): w_be owned+locked by the agent, with progress notes.
        w_be = WorkItem(
            id=101, project_id=p, title="实现群聊后端", description="block + thread + REST",
            owner=main, status=WorkItemStatus.in_progress, locked=True,
            annotation_count=2, created_at=_T0, updated_at=_T0 + 6_000,
        )
        w_fe = WorkItem(
            id=102, project_id=p, title="设计工作区前端", description="三栏 + 现场弹窗",
            status=WorkItemStatus.open, created_at=_T0 + 1_000, updated_at=_T0 + 1_000,
        )
        w_sub = WorkItem(
            id=103, project_id=p, parent_id=101, title="设计 block 表", description="双树 + block_ref",
            owner=main, status=WorkItemStatus.done, locked=True,
            annotation_count=0, created_at=_T0 + 2_000, updated_at=_T0 + 5_000,
        )
        for w in (w_be, w_fe, w_sub):
            self.workitems[w.id] = w
        self.annotations[101] = [
            WorkItemAnnotation(id=111, work_item_id=101, author=main,
                               content="已建 block/thread 模型，开始写 REST。", created_at=_T0 + 3_000),
            WorkItemAnnotation(id=112, work_item_id=101, author=alice,
                               content="记得 author_id 一律用 user_id。", created_at=_T0 + 4_000),
        ]
        self.annotations[102] = []
        self.annotations[103] = []

        # documents (文档树)
        d_req = Document(id=201, project_id=p, title="产品需求文档", updated_at=_T0 + 7_000, updated_by=main)
        d_api = Document(id=202, project_id=p, parent_id=201, title="接口设计",
                         updated_at=_T0 + 8_000, updated_by=alice)
        self.documents[201] = d_req
        self.documents[202] = d_api
        self.doc_nodes[201] = [
            DocNode(id=211, document_id=201, kind=DocNodeKind.heading, content="知是 2.0",
                    order=0, edited_at=_T0, edited_by=main),
            DocNode(id=212, document_id=201, kind=DocNodeKind.paragraph,
                    content="让 AI 队友参与项目全生命周期，过程自动留痕。", order=1,
                    edited_at=_T0, edited_by=main),
            DocNode(id=213, document_id=201, kind=DocNodeKind.todo,
                    content="初稿：群聊 / 事项 / 文档", order=2, edited_at=_T0, edited_by=alice),
        ]
        self.doc_nodes[202] = [
            DocNode(id=221, document_id=202, kind=DocNodeKind.heading, content="REST 契约",
                    order=0, edited_at=_T0, edited_by=alice),
        ]

        # threads (群聊) + messages, incl. a system claim and a workitem ref.
        t_prod = Thread(id=301, project_id=p, kind=ThreadKind.general, title="产品讨论",
                        member_count=3, members=[alice, bob, main], updated_at=_T0 + 20_000)
        t_mgmt = Thread(id=302, project_id=p, kind=ThreadKind.management, title="管理群",
                        member_count=3, members=[alice, main, data], updated_at=_T0 + 15_000)
        self.threads[301] = t_prod
        self.threads[302] = t_mgmt
        self.thread_members[301] = [ThreadMember(user=alice, role="owner"),
                                    ThreadMember(user=bob),
                                    ThreadMember(user=main,
                                                 attention_policy_override=AttentionPolicy.all_messages)]
        self.thread_members[302] = [ThreadMember(user=alice, role="owner"),
                                    ThreadMember(user=main,
                                                 attention_policy_override=AttentionPolicy.all_messages),
                                    ThreadMember(user=data,
                                                 attention_policy_override=AttentionPolicy.mention_only)]
        self.messages[301] = [
            Message(id=311, thread_id=301, author=alice, content="群聊后端谁来做？", created_at=_T0 + 10_000),
            Message(id=312, thread_id=301, author=main,
                    content="我来。已加入我的事项。", created_at=_T0 + 12_000,
                    refs=[BlockRef(target_type=RefTargetType.workitem, target_id=101, title="实现群聊后端")]),
            Message(id=313, thread_id=301, author=main, kind=MessageKind.system,
                    content="芝士·主管 认领了事项《实现群聊后端》", created_at=_T0 + 12_500,
                    refs=[BlockRef(target_type=RefTargetType.workitem, target_id=101, title="实现群聊后端")]),
            Message(id=314, thread_id=301, author=bob,
                    content="需求见文档。", created_at=_T0 + 20_000,
                    refs=[BlockRef(target_type=RefTargetType.document, target_id=201, title="产品需求文档")]),
        ]
        self.messages[302] = [
            Message(id=321, thread_id=302, author=alice,
                    content="@芝士·主管 把三棵树对齐一下。", created_at=_T0 + 15_000),
        ]


# Process-local singleton. Swapped for real repositories in Phase B/C.
STORE = MockWorkspace()
