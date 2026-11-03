"""What the platform publishes to everyone signed in is checked first.

Product rule (知是可见性与加入规则): text and pictures every signed-in user can
see — a profile, a recruitment post, a discoverable team, an open space's
problems and announcements — pass Alibaba Cloud Content Moderation when they are
written. A high-risk verdict refuses the write; a medium one asks the author to
reword it; low and none pass. A check that cannot be made refuses the write
rather than publishing unchecked, and the refusal never names what matched.
A deployment without the service's key does not boot.
"""

import pytest

from app.core.config import Settings
from app.core.content_review import (
    TextKind,
    readable_text,
    review_image,
    review_text,
)
from app.core.errors import UnprocessableEntityError, UpstreamUnavailableError
from tests.support.content_review import Green

KINDS = [TextKind.NAME, TextKind.PUBLIC_TEXT]


def _key(refusal: Exception) -> str:
    """The sentence the author is shown, as the catalog key the screen renders."""
    return refusal.to_response_body()["error"]["i18n"]["key"]


@pytest.mark.parametrize("kind", KINDS)
async def test_a_high_risk_text_is_refused_without_saying_what_matched(
    green: Green, kind: TextKind
) -> None:
    green.risk["BANNED"] = "high"
    with pytest.raises(UnprocessableEntityError) as refused:
        await review_text(kind, ["招新：BANNED 欢迎加入"])
    assert _key(refused.value) == "contentRejected"
    assert "political" not in refused.value.message
    assert "BANNED" not in refused.value.message


@pytest.mark.parametrize("kind", KINDS)
async def test_a_medium_risk_text_is_sent_back_to_be_reworded(
    green: Green, kind: TextKind
) -> None:
    green.risk["DOUBTFUL"] = "medium"
    with pytest.raises(UnprocessableEntityError) as refused:
        await review_text(kind, ["DOUBTFUL"])
    assert _key(refused.value) == "contentNeedsRevision"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("level", ["low", "none"])
async def test_a_low_or_no_risk_text_passes(
    green: Green, kind: TextKind, level: str
) -> None:
    green.risk["ordinary"] = level
    await review_text(kind, ["an ordinary nickname"])


@pytest.mark.parametrize("kind", KINDS)
async def test_an_unreachable_service_refuses_rather_than_publishing_unchecked(
    green: Green, kind: TextKind
) -> None:
    green.down = True
    with pytest.raises(UpstreamUnavailableError) as refused:
        await review_text(kind, ["anything at all"])
    assert _key(refused.value) == "contentReviewUnavailable"


@pytest.mark.parametrize("kind", KINDS)
async def test_a_service_error_answer_refuses_rather_than_publishing_unchecked(
    green: Green, kind: TextKind
) -> None:
    green.code = 408  # the account is not authorised (lapsed, unpaid)
    with pytest.raises(UpstreamUnavailableError):
        await review_text(kind, ["anything at all"])


async def test_a_word_beyond_the_services_600_characters_is_still_read(
    green: Green,
) -> None:
    green.risk["BANNED"] = "high"
    with pytest.raises(UnprocessableEntityError):
        await review_text(TextKind.PUBLIC_TEXT, ["题" * 1500 + "BANNED"])


async def test_the_worst_of_several_texts_decides(green: Green) -> None:
    green.risk["DOUBTFUL"] = "medium"
    green.risk["BANNED"] = "high"
    with pytest.raises(UnprocessableEntityError) as refused:
        await review_text(TextKind.PUBLIC_TEXT, ["DOUBTFUL", "fine", "BANNED"])
    assert _key(refused.value) == "contentRejected"


async def test_a_rich_text_document_is_read_as_the_words_it_shows() -> None:
    document = (
        '{"type":"doc","content":[{"type":"paragraph","content":'
        '[{"type":"text","text":"第一段"},{"type":"text","text":"BANNED"}]}]}'
    )
    assert readable_text(document).split() == ["第一段", "BANNED"]
    assert readable_text("<p>公告 &amp; 说明</p>").split() == ["公告", "&", "说明"]
    assert readable_text("plain words") == "plain words"


@pytest.mark.parametrize(
    ("level", "key"), [("high", "contentRejected"), ("medium", "contentNeedsRevision")]
)
async def test_a_risky_picture_is_refused(green: Green, level: str, key: str) -> None:
    green.image_risk = level
    with pytest.raises(UnprocessableEntityError) as refused:
        await review_image(b"\x89PNG picture")
    assert _key(refused.value) == key


async def test_a_clean_picture_passes_and_an_unreachable_service_refuses(
    green: Green,
) -> None:
    await review_image(b"\x89PNG picture")
    green.down = True
    with pytest.raises(UpstreamUnavailableError):
        await review_image(b"\x89PNG picture")


async def test_a_developer_machine_without_the_key_publishes_unchecked() -> None:
    # No `green` fixture: the suite's settings carry no key, as a developer's
    # machine does. Reaching the service would fail; nothing is asked.
    await review_text(TextKind.NAME, ["anything"])
    await review_image(b"\x89PNG picture")


_DEPLOYMENT = {
    "jwt_secret": "a-genuinely-random-48-char-secret-value",
    "platform_admin_handles": ["ops"],
    "data_encryption_key": "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
}


@pytest.mark.parametrize(
    "signal",
    [
        {"environment": "production"},
        {"environment": "development", "deployed_via_compose": True},
    ],
)
@pytest.mark.parametrize(
    "key",
    [
        {},
        {"aliyun_green_access_key_id": "id"},
        {"aliyun_green_access_key_secret": "secret"},
    ],
)
def test_a_deployment_without_the_key_does_not_boot(signal: dict, key: dict) -> None:
    with pytest.raises(RuntimeError) as refused:
        Settings(_env_file=None, **_DEPLOYMENT, **signal, **key)
    assert "ALIYUN_GREEN_ACCESS_KEY_ID" in str(refused.value)


def test_a_deployment_with_the_key_boots() -> None:
    booted = Settings(
        _env_file=None,
        environment="production",
        aliyun_green_access_key_id="id",
        aliyun_green_access_key_secret="secret",
        **_DEPLOYMENT,
    )
    assert booted.content_review_configured


@pytest.mark.parametrize("environment", ["development", "test"])
def test_a_developer_machine_boots_without_the_key(environment: str) -> None:
    assert not Settings(
        _env_file=None, environment=environment
    ).content_review_configured
