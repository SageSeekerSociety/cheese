"""Fixed question membership and atomic, versioned group settlement.

The caller authorizes the place before entering this service. All member locks
are acquired by UUID, never by question order; no partial answer is flushed.
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.block.answers import Answer
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.notice_text import say


def required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} 必须是非空字符串")
    return value.strip()


@dataclass(frozen=True)
class Question:
    content: str
    options: list[dict]
    allow_other: bool
    reject_option: bool

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValidationError("每题必须是对象")
        content = required_text(value.get("question"), "question")
        raw = value.get("options")
        if not isinstance(raw, list) or not 2 <= len(raw) <= 3:
            raise ValidationError("每题需要 2-3 个对象选项")
        options = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValidationError("每个选项都是 {text, explain?} 对象")
            option = {"text": required_text(item.get("text"), "text")}
            if "explain" in item:
                if not isinstance(item["explain"], str):
                    raise ValidationError("explain 必须是字符串")
                if item["explain"].strip():
                    option["explain"] = item["explain"].strip()
            options.append(option)
        if len({item["text"] for item in options}) != len(options):
            raise ValidationError("选项 text 不得重复")
        flags = []
        for field in ("allow_other", "reject_option"):
            flag = value.get(field, True)
            if not isinstance(flag, bool):
                raise ValidationError(f"{field} 必须是布尔值")
            flags.append(flag)
        return cls(content, options, *flags)


def parse_questions(body):
    raw = body.get("questions")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValidationError("questions 必须含 1-8 题")
    return [Question.parse(item) for item in raw]


class AskGroups:
    def __init__(self, session):
        self.session = session

    def query(self, topic_id, asked_by, group_id):
        return select(Block).where(
            Block.topic_id == topic_id,
            Block.author == asked_by,
            Block.meta["ask_group"]["id"].as_string() == group_id,
        )

    async def read(self, topic_id, asked_by, group_id, *, lock=False):
        query = self.query(topic_id, asked_by, group_id).order_by(Block.id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        rows = list(await self.session.scalars(query))
        if not rows:
            raise NotFoundError("问题组不存在")
        group = rows[0].meta.get("ask_group") or {}
        members = group.get("members") or []
        if (
            len(members) != len(rows)
            or len(set(members)) != len(members)
            or set(members) != {str(row.id) for row in rows}
            or group.get("total") != len(members)
        ):
            raise ConflictError("问题组成员不完整，请重新获取")
        indexed = {str(row.id): row for row in rows}
        ordered = [indexed[key] for key in members]
        for index, row in enumerate(ordered):
            expected = {
                "id": group_id,
                "members": members,
                "total": len(members),
                "index": index,
                "asked_by": asked_by,
            }
            if row.meta.get("ask_group") != expected:
                raise ConflictError("问题组归属或顺序不一致")
            for field in ("group_settle", "group_settle_log", "ask_origin"):
                if row.meta.get(field) != rows[0].meta.get(field):
                    raise ConflictError("问题组提交历史或原执行者不一致")
        return ordered

    async def create(
        self, *, project_id, topic_id, asked_by, group_id, questions, asked, origin
    ):
        # Only creation needs a namespace lock: before there are member rows,
        # concurrent callers cannot serialize by their UUIDs yet.
        scope = f"{topic_id}:{asked_by}:{group_id}"
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": scope},
        )
        existing = list(
            await self.session.scalars(self.query(topic_id, asked_by, group_id))
        )
        if existing:
            raise ConflictError("这个 ask_group 已创建，不能追加或重建")
        ids = [uuid.uuid4() for _ in questions]
        members = [str(key) for key in ids]
        rows = []
        for index, question in enumerate(questions):
            row = Block(
                id=ids[index],
                project_id=project_id,
                topic_id=topic_id,
                task_id=uuid.UUID(origin["task_id"]) if origin.get("task_id") else None,
                author=asked_by,
                author_type=AuthorType.participant,
                content=question.content,
                kind=BlockKind.message,
                turn_id=uuid.UUID(origin["work_id"]),
                meta={
                    "options": question.options,
                    "asked": asked,
                    "allow_other": question.allow_other,
                    "reject_option": question.reject_option,
                    "answer_log": [],
                    "ask_origin": origin,
                    "ask_group": {
                        "id": group_id,
                        "members": members,
                        "index": index,
                        "total": len(ids),
                        "asked_by": asked_by,
                    },
                },
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        return rows

    async def settle(self, *, topic_id, asked_by, group_id, body, author):
        operation = required_text(body.get("client_op_id"), "client_op_id")
        version = body.get("expect_version")
        if type(version) is not int or version < 0:
            raise ValidationError("expect_version 必须是非负整数")
        parsed = {}
        all_ids = []
        canonical = {
            "topic_id": str(topic_id),
            "asked_by": asked_by,
            "group_id": group_id,
            "expect_version": version,
        }
        for field in ("answered", "later", "unanswered"):
            items = body.get(field)
            if not isinstance(items, list):
                raise ValidationError(f"{field} 必须是数组")
            parsed[field] = {}
            entries = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValidationError(f"{field} 每项必须是对象")
                try:
                    key = str(
                        uuid.UUID(required_text(item.get("block_id"), "block_id"))
                    )
                except ValueError as exc:
                    raise ValidationError("block_id 必须是 UUID") from exc
                all_ids.append(key)
                if field == "answered":
                    answer = Answer.parse(item)
                    parsed[field][key] = answer
                    entries.append({"block_id": key, **asdict(answer)})
                else:
                    op = required_text(item.get("client_op_id"), "client_op_id")
                    parsed[field][key] = op
                    entries.append({"block_id": key, "client_op_id": op})
            canonical[field] = sorted(entries, key=lambda entry: entry["block_id"])
        if len(all_ids) != len(set(all_ids)):
            raise ValidationError("三列表不得有重复或交集")
        payload_hash = hashlib.sha256(
            json.dumps(
                canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        rows = await self.read(topic_id, asked_by, group_id, lock=True)
        if set(all_ids) != {str(row.id) for row in rows}:
            raise ValidationError("三列表必须恰好覆盖全部组成员")
        first = rows[0].meta or {}
        history = first.get("group_settle_log") or []
        for stored in history:
            if stored["by"] == author and stored["client_op_id"] == operation:
                if stored["payload_hash"] != payload_hash:
                    raise ConflictError(say("askGroupOpIdReused"))
                return rows, stored, True
        current = first.get("group_settle")
        if (current["v"] if current else 0) != version:
            # The browser tells this refusal from the other 409s by its key
            # (`useAskGroups`); the sentence itself is the catalog's.
            raise ConflictError(say("askGroupVersionStale"))
        staged = []
        effective = []
        for row in rows:
            meta = dict(row.meta or {})
            key = str(row.id)
            if key in parsed["answered"]:
                parsed["answered"][key].apply(meta, author)
            if meta.get("answer_log"):
                effective.append(key)
            staged.append(meta)
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ask-settle:{topic_id}:{asked_by}:{group_id}:{version + 1}",
        )
        settlement = {
            "v": version + 1,
            "at": datetime.now(UTC).isoformat(),
            "by": author,
            "answered": effective,
            "later": [
                str(row.id)
                for row in rows
                if str(row.id) in parsed["later"] and str(row.id) not in effective
            ],
            "unanswered": [
                str(row.id)
                for row in rows
                if str(row.id) in parsed["unanswered"] and str(row.id) not in effective
            ],
            "payload_hash": payload_hash,
            "operation": {**canonical, "client_op_id": operation},
            "client_op_id": operation,
            "delivery_event_id": str(event_id),
        }
        # Assign only after the last member has passed. The surrounding request
        # commits answers, one wake block and its delivery together.
        for row, meta in zip(rows, staged, strict=True):
            meta["group_settle"] = settlement
            meta["group_settle_log"] = [*history, settlement]
            row.meta = meta
        return rows, settlement, False
