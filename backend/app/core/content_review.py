"""Machine review of what the platform publishes to everyone signed in.

Text and pictures that every signed-in user can see — a profile, a recruitment
post, a discoverable team, the problems and announcements of an open space —
are information the platform publishes, and reviewing published information is
the platform's job (网络信息内容生态治理规定). Each is checked by Alibaba Cloud
Content Moderation (内容安全增强版, API 2022-03-02) when it is written; content
only the members of a team, project or course space can see is not.

The check is synchronous and decides the write:

- risk ``high`` refuses it (``contentRejected``);
- risk ``medium`` refuses it with a softer sentence (``contentNeedsRevision``):
  the service recommends a person look at those, and until there is a queue
  for that, the author rewording it is the only review it can get;
- risk ``low`` or ``none`` lets it through, as the service recommends for low
  unless recall matters more than precision.

Neither sentence names the label that matched. A failed call — network,
timeout, a non-200 answer — refuses the write too (``contentReviewUnavailable``):
publishing unchecked is exactly what this module exists to prevent, and these
writes are rare enough that "try again shortly" costs little. No retries beyond
that: the author is waiting on the answer.

Without the AccessKey (a developer's machine, the test suite) nothing is
checked; a deployment cannot boot without it
(``Settings._require_content_review_key_on_deployment``).

Services: https://help.aliyun.com/zh/document_detail/2684669.html (text, PLUS)
and https://help.aliyun.com/document_detail/467829.html (image).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import uuid
from collections.abc import Iterable
from enum import Enum

import oss2
from alibabacloud_green20220302 import models as green_models
from alibabacloud_green20220302.client import Client
from alibabacloud_tea_openapi.models import Config
from alibabacloud_tea_util.models import RuntimeOptions

from app.core.config import settings
from app.core.errors import UnprocessableEntityError, UpstreamUnavailableError
from app.core.sentences import say

logger = logging.getLogger(__name__)

ENDPOINT = "green-cip.cn-shanghai.aliyuncs.com"
REGION = "cn-shanghai"

#: The text PLUS services accept at most 600 characters a call.
TEXT_CHUNK = 600

#: Milliseconds, as the SDK takes them; the values of the service's own sample.
CONNECT_TIMEOUT_MS = 3000
READ_TIMEOUT_MS = 6000


class TextKind(Enum):
    """What a piece of text is, which picks the service that reads it."""

    #: A name others see next to everything its owner does: a nickname, a
    #: team's name.
    NAME = "nickname_detection_pro"
    #: Anything else published: an intro, a post, a problem, an announcement.
    PUBLIC_TEXT = "comment_detection_pro"


#: The service for a picture: general baseline (违法违规、色情、暴恐、政治敏感).
IMAGE_SERVICE = "baselineCheck"


class _Verdict(Enum):
    PASS = 0
    REVISE = 1
    REJECT = 2


def _verdict(risk_level: str | None) -> _Verdict:
    if risk_level == "high":
        return _Verdict.REJECT
    if risk_level == "medium":
        return _Verdict.REVISE
    return _Verdict.PASS


class ContentReviewFailed(Exception):
    """The service did not give an answer this module can act on."""


def _client() -> Client:
    return Client(
        Config(
            access_key_id=settings.aliyun_green_access_key_id,
            access_key_secret=settings.aliyun_green_access_key_secret,
            endpoint=ENDPOINT,
            region_id=REGION,
            connect_timeout=CONNECT_TIMEOUT_MS,
            read_timeout=READ_TIMEOUT_MS,
        )
    )


def readable_text(raw: str) -> str:
    """The words a reader sees in ``raw``: a rich-text document's JSON
    (``{"type": "doc", ...}``) or HTML reduced to its text, so the service reads
    what is shown rather than markup — which would also spend its 600-character
    budget on tags."""
    try:
        document = json.loads(raw)
    except ValueError:
        document = None
    if isinstance(document, dict | list):
        words: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("text"), str):
                    words.append(node["text"])
                for value in node.values():
                    if isinstance(value, dict | list):
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(document)
        return "\n".join(words)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def _chunks(text: str) -> list[str]:
    return [text[i : i + TEXT_CHUNK] for i in range(0, len(text), TEXT_CHUNK)]


async def _text_risk(client: Client, service: str, content: str) -> str | None:
    request = green_models.TextModerationPlusRequest(
        service=service,
        service_parameters=json.dumps({"content": content}, ensure_ascii=False),
    )
    try:
        response = await client.text_moderation_plus_with_options_async(
            request, RuntimeOptions()
        )
    except Exception as exc:
        raise ContentReviewFailed(f"{type(exc).__name__}: {exc}") from exc
    body = response.body
    if body is None or body.code != 200 or body.data is None:
        raise ContentReviewFailed(
            f"answered code {getattr(body, 'code', None)}: "
            f"{getattr(body, 'message', None)}"
        )
    return body.data.risk_level


def _raise_for(verdict: _Verdict) -> None:
    if verdict is _Verdict.REJECT:
        raise UnprocessableEntityError(say("contentRejected"))
    if verdict is _Verdict.REVISE:
        raise UnprocessableEntityError(say("contentNeedsRevision"))


def _unavailable(what: str, exc: ContentReviewFailed) -> UpstreamUnavailableError:
    # An error, not a warning: every write it refuses is a person who could not
    # publish, and an account that lapsed (code 408) refuses them all.
    logger.error("content review of %s failed: %s", what, exc)
    return UpstreamUnavailableError(say("contentReviewUnavailable"))


async def review_text(kind: TextKind, texts: Iterable[str | None]) -> None:
    """Refuse, with the sentence the author is shown, unless every one of
    ``texts`` may be published. Empty and missing texts need no review."""
    pieces = [
        chunk for text in texts if text and text.strip() for chunk in _chunks(text)
    ]
    if not pieces or not settings.content_review_configured:
        return
    client = _client()
    try:
        risks = await asyncio.gather(
            *(_text_risk(client, kind.value, piece) for piece in pieces)
        )
    except ContentReviewFailed as exc:
        raise _unavailable(kind.name, exc) from exc
    _raise_for(max((_verdict(risk) for risk in risks), key=lambda v: v.value))


def _put_for_review(
    token: green_models.DescribeUploadTokenResponseBodyData, content: bytes
) -> str:
    """Store ``content`` in the service's own short-lived bucket (it keeps a
    file 30 minutes) and name the object: the service reads a picture from a
    URL or from that bucket, and the deployment's own URLs need not be
    reachable from it."""
    bucket = oss2.Bucket(
        oss2.StsAuth(
            token.access_key_id, token.access_key_secret, token.security_token
        ),
        f"https://{token.oss_internet_end_point}",
        token.bucket_name,
    )
    name = f"{token.file_name_prefix}{uuid.uuid4().hex}"
    bucket.put_object(name, content)
    return name


async def _image_risk(client: Client, content: bytes) -> str | None:
    try:
        # A token per picture rather than one kept until it expires: avatars are
        # uploaded rarely, and a cached token is state to keep right.
        token = (await client.describe_upload_token_async()).body
        if token is None or token.code != 200 or token.data is None:
            raise ContentReviewFailed(
                f"upload token answered code {getattr(token, 'code', None)}"
            )
        name = await asyncio.to_thread(_put_for_review, token.data, content)
        request = green_models.ImageModerationRequest(
            service=IMAGE_SERVICE,
            service_parameters=json.dumps(
                {
                    "ossBucketName": token.data.bucket_name,
                    "ossObjectName": name,
                    "dataId": uuid.uuid4().hex,
                }
            ),
        )
        response = await client.image_moderation_with_options_async(
            request, RuntimeOptions()
        )
    except ContentReviewFailed:
        raise
    except Exception as exc:
        raise ContentReviewFailed(f"{type(exc).__name__}: {exc}") from exc
    body = response.body
    if body is None or body.code != 200 or body.data is None:
        raise ContentReviewFailed(
            f"answered code {getattr(body, 'code', None)}: {getattr(body, 'msg', None)}"
        )
    return body.data.risk_level


async def review_image(content: bytes) -> None:
    """Refuse, with the sentence the author is shown, unless the picture
    ``content`` may be published."""
    if not settings.content_review_configured:
        return
    try:
        risk = await _image_risk(_client(), content)
    except ContentReviewFailed as exc:
        raise _unavailable("image", exc) from exc
    _raise_for(_verdict(risk))
