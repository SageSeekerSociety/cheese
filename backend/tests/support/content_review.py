"""Alibaba Cloud Content Moderation, for tests: the client the platform calls,
answering the way the service does — the SDK's own response models — with the
risk a test picked for the words it publishes.

A test says what the service thinks (``green.risk["BANNED"] = "high"``), what it
thinks of pictures (``green.image_risk``), or that it cannot be reached
(``green.down = True``), and then checks what the platform did with the answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from alibabacloud_green20220302 import models as green_models
from Tea.exceptions import UnretryableException

from app.core import content_review
from app.core.config import settings

_ORDER = ("none", "low", "medium", "high")


@dataclass
class Green:
    #: A word, and the risk level the service gives any text containing it.
    risk: dict[str, str] = field(default_factory=dict)
    #: The risk level the service gives every picture.
    image_risk: str = "none"
    #: The service answers with this code instead of 200 (an outage, a lapsed
    #: account).
    code: int = 200
    #: The connection fails before any answer.
    down: bool = False
    #: What the service was asked to read, text by text.
    read: list[str] = field(default_factory=list)
    pictures: list[bytes] = field(default_factory=list)

    def _answer_or_raise(self) -> None:
        if self.down:
            raise UnretryableException(ConnectionError("connection refused"))

    async def text_moderation_plus_with_options_async(self, request, runtime):
        self._answer_or_raise()
        content = json.loads(request.service_parameters)["content"]
        self.read.append(content)
        level = max(
            (lvl for word, lvl in self.risk.items() if word in content),
            key=_ORDER.index,
            default="none",
        )
        return green_models.TextModerationPlusResponse(
            status_code=200,
            body=green_models.TextModerationPlusResponseBody(
                code=self.code,
                message="OK" if self.code == 200 else "GENERAL_ERROR",
                data=green_models.TextModerationPlusResponseBodyData(
                    risk_level=level,
                    result=[
                        green_models.TextModerationPlusResponseBodyDataResult(
                            label="political_content" if level != "none" else "nonLabel"
                        )
                    ],
                )
                if self.code == 200
                else None,
            ),
        )

    async def describe_upload_token_async(self):
        self._answer_or_raise()
        return green_models.DescribeUploadTokenResponse(
            status_code=200,
            body=green_models.DescribeUploadTokenResponseBody(
                code=200,
                data=green_models.DescribeUploadTokenResponseBodyData(
                    access_key_id="sts-id",
                    access_key_secret="sts-secret",
                    security_token="sts-token",
                    bucket_name="green-upload",
                    file_name_prefix="upload/",
                    oss_internet_end_point="oss-cn-shanghai.aliyuncs.com",
                ),
            ),
        )

    def put(self, token, content: bytes) -> str:
        self.pictures.append(content)
        return f"{token.file_name_prefix}{len(self.pictures)}"

    async def image_moderation_with_options_async(self, request, runtime):
        self._answer_or_raise()
        return green_models.ImageModerationResponse(
            status_code=200,
            body=green_models.ImageModerationResponseBody(
                code=self.code,
                data=green_models.ImageModerationResponseBodyData(
                    risk_level=self.image_risk
                )
                if self.code == 200
                else None,
            ),
        )


def install(monkeypatch) -> Green:
    """Configure the platform with a key and point it at a fresh :class:`Green`."""
    green = Green()
    monkeypatch.setattr(settings, "aliyun_green_access_key_id", "test-key-id")
    monkeypatch.setattr(settings, "aliyun_green_access_key_secret", "test-secret")
    monkeypatch.setattr(content_review, "_client", lambda: green)
    monkeypatch.setattr(content_review, "_put_for_review", green.put)
    return green
