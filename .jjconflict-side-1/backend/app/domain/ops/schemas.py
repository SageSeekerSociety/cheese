"""Operation-card response schemas (Pydantic v2)."""

from pydantic import BaseModel, ConfigDict, JsonValue


class OperationCardFace(BaseModel):
    """The 七问 as the card shows them.

    `operation_id` + `args` are what the author asked for; everything below them
    is derived from the registry — see `domain/ops/registry.py` for why the card
    never renders author-written risk text.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    operation_id: str
    topic_id: str
    requested_by: str
    reason: str
    authorization: str
    args: dict[str, JsonValue]
    what: str
    where: str
    blast_radius: str
    reversibility: str
    reversal: str
    worst_case: str
    interruptible: bool
    human_approvals_required: int


class OperationRequestOut(BaseModel):
    """One manifest found on a topic's branch.

    An invalid manifest is reported, not hidden: a request that fails validation
    must be visible on the card as "这张没过校验", otherwise a broken request
    looks exactly like no request at all.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    valid: bool
    issues: list[str] = []
    face: OperationCardFace | None = None


__all__ = ["OperationCardFace", "OperationRequestOut"]
