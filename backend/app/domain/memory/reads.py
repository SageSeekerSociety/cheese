"""Count memory-body reads from raw tool calls, including sealed records."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.models import Conversation
from app.domain.session_record import service
from app.domain.session_record.views import parts

MEMORY_DIR_MARKER = ".cheese/memory/"

#: 索引文件不算正文。
INDEX_NAME = "MEMORY.md"

READ_TOOL = "Read"


def is_body_read(tool: object, detail: object) -> bool:
    """Only complete Read paths below the memory directory count; indexes do not."""
    if not isinstance(tool, str) or tool != READ_TOOL:
        return False
    if not isinstance(detail, str):
        return False
    normalized = detail.replace("\\", "/")
    marker = normalized.find(MEMORY_DIR_MARKER)
    if marker < 0:
        return False
    relative = normalized[marker + len(MEMORY_DIR_MARKER) :]
    return relative != INDEX_NAME and not relative.endswith(f"/{INDEX_NAME}")


@dataclass(frozen=True)
class DayReads:
    day: str
    reads: int


@dataclass(frozen=True)
class ProjectReads:
    """一个项目在这个窗口里，每天读了多少次正文。"""

    project_id: str
    total: int
    days: list[DayReads]

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "total": self.total,
            "days": [{"day": day.day, "reads": day.reads} for day in self.days],
        }


async def body_reads(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    project_id: uuid.UUID | None = None,
) -> list[ProjectReads]:
    query = select(Conversation.id, Conversation.project_id)
    if project_id is not None:
        query = query.where(Conversation.project_id == project_id)
    counts: dict[str, dict[str, int]] = {}
    for conversation, project in (await session.execute(query)).all():
        cursor = (since, "", "", -1)
        while True:
            page = await service.read(
                session,
                conversation,
                after=cursor,
                before=(until, "", "", -1),
                reader=None,
            )
            for item in page.items:
                for part in parts(item["record"]):
                    args = part.get("input") or {}
                    if part["kind"] == "tool" and is_body_read(
                        part["name"], args.get("file_path") or args.get("path")
                    ):
                        day = (
                            datetime.fromisoformat(item["at"])
                            .astimezone(UTC)
                            .date()
                            .isoformat()
                        )
                        days = counts.setdefault(str(project), {})
                        days[day] = days.get(day, 0) + 1
            if page.next is None:
                break
            cursor = service.decode_cursor(page.next)
    return [
        ProjectReads(
            project_id=project,
            total=sum(days.values()),
            days=[
                DayReads(day=day, reads=count) for day, count in sorted(days.items())
            ],
        )
        for project, days in sorted(counts.items())
    ]
