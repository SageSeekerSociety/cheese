"""Answer rules shared by single-question and atomic group submissions.

Callers authenticate and lock before applying these rules. This module does no
I/O, writes no delivery and never commits a partially validated group.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.errors import ConflictError, ValidationError


@dataclass(frozen=True)
class Answer:
    kind: str
    option: str
    note: str
    client_op_id: str
    expect_version: int

    @classmethod
    def parse(cls, body: dict) -> "Answer":
        kind = (body.get("kind") or "option").strip()
        option = (body.get("option") or "").strip()
        note = (body.get("note") or "").strip()
        client_op_id = (body.get("client_op_id") or "").strip()
        expect_version = body.get("expect_version")
        if kind not in ("option", "note", "reject"):
            raise ValidationError("kind 要是 option / note / reject")
        if not client_op_id:
            raise ValidationError("client_op_id 必带")
        if not isinstance(expect_version, int) or isinstance(expect_version, bool):
            raise ValidationError(
                "expect_version 必带：初答 0，之后是 answer_log 末项的 v"
            )
        if len(note) > 2000:
            raise ValidationError("note 最多 2000 字")
        return cls(kind, option, note, client_op_id, expect_version)

    def apply(self, meta: dict, author: str) -> tuple[dict, bool]:
        """Return the answer and replay flag; append only after every check passes."""
        if author == "anonymous":
            raise ValidationError("要登录才能作答")
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
                raise ConflictError("同一个 client_op_id 换了内容")
            return entry, True

        texts = [
            entry["text"] if isinstance(entry, dict) else str(entry)
            for entry in meta.get("options") or []
        ]
        if self.kind == "option":
            if not self.option:
                raise ValidationError("kind=option 要给 option")
            if self.option not in texts:
                raise ValidationError("不在选项里")
        else:
            if self.option:
                raise ValidationError(f"kind={self.kind} 不给 option")
            if self.kind == "note" and not self.note:
                raise ValidationError("kind=note 要给 note")
            if self.kind == "note" and not meta.get("allow_other"):
                raise ValidationError("这道题不接受自由输入")
        if self.kind == "reject" and not (
            meta.get("reject_option") or meta.get("allow_other")
        ):
            raise ValidationError("这道题不接受「以上都不是」")
        if log and author != log[-1].get("by"):
            raise ValidationError("只有原答者能更正")
        if len(log) != self.expect_version:
            raise ConflictError("版本不对，请重取这一题后再作答")
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
