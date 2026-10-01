"""Only raw UTF-8 spans can authorize a proposal; display quotes cannot."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def parse_uuid(value):
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("UUID must be a string")
    return uuid.UUID(value)


UuidInput = Annotated[uuid.UUID, BeforeValidator(parse_uuid)]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceSelection(StrictInput):
    node_id: UuidInput
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    exact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("原文范围必须非空且有序")
        return self


class RequestIn(StrictInput):
    operation_id: UuidInput
    kind: Literal["ask", "propose"]
    question: str = Field(min_length=1, max_length=16000)
    document_id: UuidInput
    base_version: int = Field(ge=1)
    selection: SourceSelection | None = None

    @model_validator(mode="after")
    def executable_selection(self):
        if self.kind == "propose" and self.selection is None:
            raise ValueError("生成提案需要经过核验的原文范围")
        return self


class AcceptIn(StrictInput):
    operation_id: UuidInput
    expected_version: int = Field(ge=1)
    revision: int = Field(ge=1)


class AskResult(StrictInput):
    answer: str = Field(min_length=1, max_length=64000)


class ProposalResult(AskResult):
    replacement: str = Field(max_length=128000)


class CompletionUsage(StrictInput):
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0, allow_inf_nan=False)
    upstream_id: str = Field(min_length=1)
