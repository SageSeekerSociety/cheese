import pytest

from app.api.byte_response import media_bytes_response


def test_media_bytes_response_supports_single_range():
    response = media_bytes_response(
        b"0123456789",
        "video/mp4",
        {"Content-Disposition": "inline"},
        "bytes=3-6",
        None,
    )

    assert response.status_code == 206
    assert response.body == b"3456"
    assert response.headers["content-range"] == "bytes 3-6/10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"


def test_media_bytes_response_rejects_unsatisfiable_range():
    response = media_bytes_response(b"0123", "video/mp4", {}, "bytes=8-", None)

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */4"
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize(
    ("header", "expected", "span"),
    [
        ("bytes=2-", b"2345", "bytes 2-5/6"),
        ("bytes=-2", b"45", "bytes 4-5/6"),
        ("bytes=0-999", b"012345", "bytes 0-5/6"),
        ("bytes=-999", b"012345", "bytes 0-5/6"),
    ],
)
def test_open_ended_and_suffix_ranges(header, expected, span):
    response = media_bytes_response(b"012345", "video/webm", {}, header, None)
    assert response.status_code == 206
    assert response.body == expected
    assert response.headers["content-range"] == span


@pytest.mark.parametrize(
    "header", ["bytes=-0", "bytes=4-1", "bytes=-", "bytes=9-", "bytes=oops"]
)
def test_invalid_or_empty_ranges_have_no_bytes(header):
    response = media_bytes_response(b"012345", "video/mp4", {}, header, None)
    assert response.status_code == 416
    assert response.body == b""


@pytest.mark.parametrize(
    ("header", "validator"),
    [
        (None, None),
        ("items=1-2", None),
        ("bytes=0-1,4-5", None),
        ("bytes=1-2", '"old"'),
    ],
)
def test_ignored_ranges_return_the_complete_current_file(header, validator):
    response = media_bytes_response(b"012345", "video/mp4", {}, header, validator)
    assert response.status_code == 200
    assert response.body == b"012345"
