"""一条运行记录推下来的那一帧里带着它自己的游标。

现场那一窗涨过上限时按读者的位置裁：停在末尾的人留下的是最新那一截，裁掉的是最旧
的。再往上翻，就从手上最早的那一条接着读——推下来的这一帧和读回来的那一页因此必须是
同一种行，都得有游标。socket 送来的行没有游标的话，裁完就没有起点了。
"""

import uuid
from datetime import UTC, datetime

from app.domain.run_record.models import RunRecord
from app.domain.run_record.service import as_payload
from app.domain.session_record.service import decode_cursor, encode_cursor

WHEN = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


def _record(seat: str | None = "cheese-a") -> RunRecord:
    return RunRecord(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        seat=seat,
        kind="turn",
        content="一句话",
        meta=None,
        created_at=WHEN,
        updated_at=WHEN,
    )


def test_the_frame_carries_the_same_cursor_a_page_hands_out():
    record = _record()

    payload = as_payload(record)

    # 读回来的一页就是这么编的（`topics_transcript` 里那条路）：位置、family、座位、id。
    assert payload["meta"]["cursor"] == encode_cursor(
        (WHEN, "platform", "cheese-a", record.id.int)
    )
    assert decode_cursor(payload["meta"]["cursor"]) == (
        WHEN,
        "platform",
        "cheese-a",
        record.id.int,
    )


def test_a_record_with_no_seat_is_signed_the_way_a_page_signs_it():
    payload = as_payload(_record(seat=None))

    # 两处说的是同一个人：`author` 和游标里的座位不能各说一套。
    assert payload["author"] == "system"
    assert decode_cursor(payload["meta"]["cursor"])[2] == "system"
