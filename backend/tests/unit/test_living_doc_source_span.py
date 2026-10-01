"""Raw offsets authorize exactly one span; quotes never choose an occurrence."""

import pytest

from app.core.errors import ConflictError, ValidationError
from app.domain.living_doc.services import content_hash
from app.domain.living_doc.source_span import replace_span, source_span


def test_byte_range_preserves_every_byte_outside_second_repeated_quote():
    source = "😀\r\n**重复句**\r\n**重复句**\r\né"
    selected = "重复句"
    start = source.encode().rindex(selected.encode())
    end = start + len(selected.encode())
    result = replace_span(
        source,
        start=start,
        end=end,
        exact_hash=content_hash(selected),
        replacement="新字😀",
    )
    assert result.encode()[:start] == source.encode()[:start]
    assert result.encode()[start + len("新字😀".encode()) :] == source.encode()[end:]
    assert result == "😀\r\n**重复句**\r\n**新字😀**\r\né"


@pytest.mark.parametrize("start,end", [(1, 4), (0, 3), (-1, 4), (4, 4), (0, 99)])
def test_invalid_utf8_boundary_or_range_is_refused(start, end):
    with pytest.raises(ValidationError):
        source_span("😀中文", start, end, content_hash("😀"))


def test_same_quote_elsewhere_does_not_authorize_a_stale_exact_span():
    with pytest.raises(ConflictError):
        replace_span(
            "原句\r\n别句",
            start=0,
            end=6,
            exact_hash=content_hash("别句"),
            replacement="不能写",
        )
