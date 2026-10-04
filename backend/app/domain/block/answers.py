"""Answer rules shared by single-question and atomic group submissions.

Callers authenticate and lock before applying these rules. This module does no
I/O, writes no delivery and never commits a partially validated group.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.errors import ConflictError, ValidationError
from app.core.sentences import say


@dataclass(frozen=True)
class Answer:
    kind: str
    option: str
    note: str
    client_op_id: str
    expect_version: int

    @classmethod
    def parse(cls, body: dict) -> "Answer":
        values = {}
        for field, default in (
            ("kind", "option"),
            ("option", ""),
            ("note", ""),
            ("client_op_id", ""),
        ):
            value = body.get(field, default)
            if not isinstance(value, str):
                raise ValidationError(say("askFieldString", field=field))
            values[field] = value.strip()
        kind, option, note, client_op_id = (
            values[field] for field in ("kind", "option", "note", "client_op_id")
        )
        expect_version = body.get("expect_version")
        if kind not in ("option", "note", "reject"):
            raise ValidationError(say("answerKindInvalid"))
        if not client_op_id:
            raise ValidationError(say("answerOpIdRequired"))
        if not isinstance(expect_version, int) or isinstance(expect_version, bool):
            raise ValidationError(say("answerVersionRequired"))
        if len(note) > 2000:
            raise ValidationError(say("answerNoteTooLong"))
        return cls(kind, option, note, client_op_id, expect_version)

    def apply(self, meta: dict, author: str) -> tuple[dict, bool]:
        """Return the answer and replay flag; append only after every check passes."""
        if author == "anonymous":
            raise ValidationError(say("answerSignIn"))
        log = list(meta.get("answer_log") or [])
        for entry in log:
            if (
                entry.get("by") != author
                or entry.get("client_op_id") != self.client_op_id
            ):
                continue
            stored = (
                entry.get("kind") or "option",
                entry.get("option") or "",
                entry.get("note") or "",
            )
            if stored != (self.kind, self.option, self.note):
                raise ConflictError(say("answerOpIdReused"))
            return entry, True

        texts = [
            entry["text"] if isinstance(entry, dict) else str(entry)
            for entry in meta.get("options") or []
        ]
        if self.kind == "option":
            if not self.option:
                raise ValidationError(say("answerOptionRequired"))
            if self.option not in texts:
                raise ValidationError(say("optionNotOffered"))
        else:
            if self.option:
                raise ValidationError(say("answerOptionNotAllowed", kind=self.kind))
            if self.kind == "note" and not self.note:
                raise ValidationError(say("answerNoteRequired"))
            if self.kind == "note" and not meta.get("allow_other"):
                raise ValidationError(say("answerNoteNotAllowed"))
        if self.kind == "reject" and not (
            meta.get("reject_option") or meta.get("allow_other")
        ):
            raise ValidationError(say("answerRejectNotAllowed"))
        if log and author != log[-1].get("by"):
            raise ValidationError(say("answerCorrectionAuthorOnly"))
        if len(log) != self.expect_version:
            raise ConflictError(say("answerVersionStale"))
        entry = {
            "v": self.expect_version + 1,
            "kind": self.kind,
            "option": self.option if self.kind == "option" else None,
            "note": self.note or None,
            "by": author,
            "at": datetime.now(UTC).isoformat(),
            "client_op_id": self.client_op_id,
        }
        meta["answer_log"] = [*log, entry]
        return entry, False
