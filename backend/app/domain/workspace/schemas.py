"""Wire schemas for the 知是 2.0 workspace (群聊 / 文档 / 事项).

These Pydantic models ARE the Phase-A API contract. They are deliberately the
same shapes the real Phase-B/C services will return, so the frontend and the
`cheese` CLI (both driven off the live OpenAPI) never have to change when the
mock backing is swapped for real `block` / `thread` / `document` / `workitem`
domains.

Naming: Python fields are snake_case; every model serializes to camelCase via the
alias generator, matching the frontend's convention. Timestamps are epoch-ms ints
(the codebase-wide convention). The API never distinguishes human from agent — an
agent is a user; `UserSummary.is_agent` / `agent_status` are presentation-only
hints derived at the edge (does this user have an execution binding?), not a
domain branch.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True
    )


# ── shared ────────────────────────────────────────────────────────────────────


class AgentStatus(str, Enum):
    idle = "idle"
    working = "working"  # avatar shows the 现场 ring


class UserSummary(CamelModel):
    id: int
    username: str
    nickname: str
    avatar_id: int | None = None
    is_agent: bool = False
    agent_status: AgentStatus | None = None


class RefTargetType(str, Enum):
    block = "block"
    document = "document"
    workitem = "workitem"


class BlockRef(CamelModel):
    """A typed reference embedded in a message — clicking it navigates the
    right-hand context pane to that object (对话是过程,点引用看状态)."""

    target_type: RefTargetType
    target_id: int
    title: str | None = None


class Page(CamelModel):
    page_start: int | None = None
    page_size: int
    has_more: bool
    next_start: int | None = None


# ── 群聊 (threads) ──────────────────────────────────────────────────────────────


class ThreadKind(str, Enum):
    general = "general"
    management = "management"  # 管理树 is realized by a management group


class AttentionPolicy(str, Enum):
    all_messages = "ALL_MESSAGES"
    all_user_messages = "ALL_USER_MESSAGES"
    idle_window = "IDLE_WINDOW"
    mention_only = "MENTION_ONLY"


class MessageKind(str, Enum):
    text = "text"
    system = "system"  # "X 已加入事项…" claim announcements, join/leave, etc.


class Message(CamelModel):
    """A chat message is a `block` with a thread and an optional reply parent."""

    id: int
    thread_id: int
    author: UserSummary
    kind: MessageKind = MessageKind.text
    reply_to_id: int | None = None
    content: str
    refs: list[BlockRef] = []
    created_at: int
    edited_at: int | None = None


class ThreadMember(CamelModel):
    user: UserSummary
    role: str = "member"
    attention_policy_override: AttentionPolicy | None = None


class Thread(CamelModel):
    id: int
    project_id: int
    parent_thread_id: int | None = None
    kind: ThreadKind = ThreadKind.general
    title: str
    member_count: int
    members: list[UserSummary] = []  # a few, for avatars
    last_message: Message | None = None
    unread: int = 0
    updated_at: int


class CreateThreadRequest(CamelModel):
    title: str
    kind: ThreadKind = ThreadKind.general
    parent_thread_id: int | None = None
    member_ids: list[int] = []


class PostMessageRequest(CamelModel):
    content: str
    reply_to_id: int | None = None
    refs: list[BlockRef] = []


class AddMemberRequest(CamelModel):
    user_id: int
    role: str = "member"
    attention_policy_override: AttentionPolicy | None = None


# ── 文档 (documents) ────────────────────────────────────────────────────────────


class DocNodeKind(str, Enum):
    heading = "heading"
    paragraph = "paragraph"
    bullet = "bullet"
    todo = "todo"
    code = "code"


class DocNode(CamelModel):
    """A document node is a `block` on the struct tree — editable (content /
    edited_at change), unlike append-only chat blocks."""

    id: int
    document_id: int
    struct_parent_id: int | None = None
    kind: DocNodeKind = DocNodeKind.paragraph
    content: str
    order: int = 0
    edited_at: int | None = None
    edited_by: UserSummary | None = None


class Document(CamelModel):
    id: int
    project_id: int
    parent_id: int | None = None  # 文档树
    title: str
    updated_at: int
    updated_by: UserSummary | None = None


class DocumentDetail(CamelModel):
    document: Document
    nodes: list[DocNode] = []


class CreateDocumentRequest(CamelModel):
    title: str
    parent_id: int | None = None


class CreateDocNodeRequest(CamelModel):
    kind: DocNodeKind = DocNodeKind.paragraph
    content: str = ""
    struct_parent_id: int | None = None
    order: int | None = None


class EditDocNodeRequest(CamelModel):
    content: str


# ── 事项 (workitems) ────────────────────────────────────────────────────────────


class WorkItemStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"


class WorkItem(CamelModel):
    id: int
    project_id: int
    parent_id: int | None = None  # 事项树
    title: str
    description: str = ""
    owner: UserSummary | None = None
    status: WorkItemStatus = WorkItemStatus.open
    locked: bool = False  # set True the moment an owner claims it
    annotation_count: int = 0
    created_at: int
    updated_at: int


class WorkItemAnnotation(CamelModel):
    id: int
    work_item_id: int
    author: UserSummary
    content: str
    created_at: int


class WorkItemDetail(CamelModel):
    work_item: WorkItem
    annotations: list[WorkItemAnnotation] = []


class CreateWorkItemRequest(CamelModel):
    title: str
    description: str = ""
    parent_id: int | None = None


class UpdateWorkItemStatusRequest(CamelModel):
    status: WorkItemStatus


class AddAnnotationRequest(CamelModel):
    content: str
