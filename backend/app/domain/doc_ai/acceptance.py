"""Apply only an already stored proposal, through the ordinary canonical CAS."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.block.doc_selection import DocumentSelections
from app.domain.doc_ai.schemas import AcceptIn
from app.domain.doc_ai.services import DocAiService
from app.domain.living_doc.services import DocumentJournal, content_hash
from app.domain.living_doc.source_span import replace_span
from app.domain.topic.services import TopicService


class ProposalAcceptance:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def apply(
        self,
        *,
        room_id: uuid.UUID,
        proposal_id: uuid.UUID,
        body: AcceptIn,
        verified_actor: str,
        author: str,
    ):
        # All canonical operations take the room lock before proposal locks.
        # There is no reverse ordering that could deadlock two acceptors.
        journal = DocumentJournal(self.session)
        operation = await journal.claim(
            room_id=room_id,
            actor=verified_actor,
            action="ai-accept",
            operation_id=body.operation_id,
            payload={
                "proposal_id": str(proposal_id),
                "expected_version": body.expected_version,
                "revision": body.revision,
            },
        )
        if operation.receipt is not None:
            return operation.receipt, None
        proposal, request = await DocAiService(self.session).proposal(
            room_id, proposal_id, lock=True
        )
        if proposal.state != "pending" or request.state != "succeeded":
            raise ConflictError("提案已经采纳、撤回或请求未成功")
        if (
            proposal.revision != body.revision
            or request.base_version != body.expected_version
        ):
            raise ConflictError("提案或文档版本已经变化")
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
        doc, notice = await TopicService(self.session).edit_doc(
            topic_id=room_id,
            content=content,
            author=author,
            expected_version=request.base_version,
            operation_id=body.operation_id,
        )
        proposal.state = "accepted"
        proposal.accepted_by = verified_actor
        proposal.accepted_version = doc.doc_version
        receipt = {
            "proposal_id": str(proposal.id),
            "revision": proposal.revision,
            "document_id": str(doc.id),
            "content": doc.content,
            "content_hash": content_hash(doc.content),
            "doc_version": doc.doc_version,
            "operation_id": str(body.operation_id),
            "accepted_by": verified_actor,
        }
        await journal.finish(operation, receipt)
        return receipt, notice
