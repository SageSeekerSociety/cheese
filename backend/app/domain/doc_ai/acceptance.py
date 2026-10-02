"""Apply only an already stored proposal, through the live document.

Acceptance is two halves on either side of the collaboration service.
``prepare`` checks the proposal against the stored version it was made from and
computes the document it produces; the route hands that to the live document.
The service stores the result, and ``complete`` runs inside that store's
transaction: it takes the proposal lock, refuses a proposal somebody else
already settled, and marks it accepted at the version just recorded.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.block.doc_selection import DocumentSelections
from app.domain.block.models import Block
from app.domain.doc_ai.schemas import AcceptIn
from app.domain.doc_ai.services import DocAiService
from app.domain.living_doc.services import content_hash
from app.domain.living_doc.source_span import replace_span

ACTION = "ai-accept"


class ProposalAcceptance:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def prepare(
        self, *, room_id: uuid.UUID, proposal_id: uuid.UUID, body: AcceptIn
    ) -> tuple[str, str]:
        """The proposal's source and the document accepting it produces."""
        proposal, request = await DocAiService(self.session).proposal(
            room_id, proposal_id
        )
        _require_open(proposal, request, body)
        if request.selection is None or request.source_hash != content_hash(
            request.source
        ):
            raise ConflictError("提案没有完整的原文授权范围")
        doc = await DocumentSelections(self.session).snapshot(
            room_id=room_id,
            document_id=request.document_id,
            base_version=request.base_version,
            selection=request.selection,
        )
        if doc.content != request.source:
            raise ConflictError("提案的原文快照已经变化")
        span = request.selection
        content = replace_span(
            doc.content,
            start=span["start"],
            end=span["end"],
            exact_hash=span["exact_hash"],
            replacement=proposal.replacement,
        )
        return request.source, content

    async def complete(
        self,
        *,
        room_id: uuid.UUID,
        payload: dict,
        operation_id: uuid.UUID,
        verified_actor: str,
        doc: Block,
    ) -> dict:
        """Settle the proposal at ``doc``'s version; the store's receipt."""
        proposal, request = await DocAiService(self.session).proposal(
            room_id, uuid.UUID(payload["proposal_id"]), lock=True
        )
        _require_open(
            proposal,
            request,
            AcceptIn(
                operation_id=operation_id,
                expected_version=payload["expected_version"],
                revision=payload["revision"],
            ),
        )
        proposal.state = "accepted"
        proposal.accepted_by = verified_actor
        proposal.accepted_version = doc.doc_version
        return {
            "proposal_id": str(proposal.id),
            "revision": proposal.revision,
            "document_id": str(doc.id),
            "content": doc.content,
            "content_hash": content_hash(doc.content),
            "doc_version": doc.doc_version,
            "operation_id": str(operation_id),
            "accepted_by": verified_actor,
        }


def operation_payload(proposal_id: uuid.UUID, body: AcceptIn) -> dict:
    return {
        "proposal_id": str(proposal_id),
        "expected_version": body.expected_version,
        "revision": body.revision,
    }


def _require_open(proposal, request, body: AcceptIn) -> None:
    if proposal.state != "pending" or request.state != "succeeded":
        raise ConflictError("提案已经采纳、撤回或请求未成功")
    if (
        proposal.revision != body.revision
        or request.base_version != body.expected_version
    ):
        raise ConflictError("提案或文档版本已经变化")
