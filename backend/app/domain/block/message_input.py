"""Typed data accepted by the room's single message door."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.sentences import say


class SlidePageQuoteIn(BaseModel):
    """A page of text, or a passage selected inside one."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["slide-page"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    page: int = Field(gt=0, strict=True)
    # 读者指的是整页还是这一页里的一段；决定了 `text` 是整页文字还是选中的那段。
    # 默认成整页是为了读得懂这之前发出来的消息 —— 那时候还没有这个字段，而能有的
    # 只有整页。新发的一律显式带上。
    scope: Literal["page", "selection"] = "page"
    text: str
    # 选中一段两侧的文字，帮受话人分辨同一句话在这一页的哪一处出现（形状同
    # `TextRangeQuoteIn` 的 `prefix`/`suffix`）。只有 `selection` 才有：整页本来
    # 就是整页，没有「哪一处」可分。可选是因为在这之前发出的引用不带它们；上限
    # 是前端 32 个码点那个截法的两倍，容得下另一种归一化，同时挡住塞进提示词的
    # 长串。
    prefix: str = Field(default="", max_length=64)
    suffix: str = Field(default="", max_length=64)


class PagePinQuoteIn(BaseModel):
    """A point on a page: `x`/`y` are ratios of the page's width and height, not pixels.

    Ratios because the page is redrawn to the panel width and readers zoom: a pixel
    coordinate only holds for the revision it was taken on, a ratio points back to the
    same spot on any of them.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["page-pin"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    page: int = Field(gt=0, strict=True)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class SheetCellQuoteIn(BaseModel):
    """One cell of a spreadsheet, addressed the way the file itself addresses it.

    `address` is the A1 form the reader's file already carries, so the recipient
    returns to that cell without a conversion in between. CSV has no sheet name,
    which is why `sheet` may be empty rather than absent.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sheet-cell"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    sheet: str
    address: str = Field(min_length=1)
    value: str


class TextRangeQuoteIn(BaseModel):
    """A passage picked out of rendered document text.

    Text has no page number to point at: editing one sentence reflows the file,
    so a line number stops holding. The location is the heading it sits under
    (None at the top of the file) plus the context on either side, which is what
    tells one occurrence of a sentence from another.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text-range"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    text: str
    heading: str | None = None
    prefix: str
    suffix: str


class WebRectIn(BaseModel):
    """位置引用共用的矩形：`x`/`y` 是左上角，`w`/`h` 是宽高，单位都是视口像素。

    像素而不是比例，是因为网页没有稳定的「页面」可以归一到哪儿去 —— 视口宽度随
    窗口变，两者一起记下来才说得清这块区域当时指的是哪一段版面。数值不设死上限，
    但要求有限：`inf`/`nan` 穿过 JSON 会变成读不懂的东西。

    左上角 `x`/`y` 可以是负的：运行时直接量元素的 `getBoundingClientRect()`，被滚到
    视口左边、上边的元素就是负数，这也正是「它有一部分在视口外」的意思，钳成 0 反而
    把位置说错了。宽高才要求非负。
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    w: float = Field(ge=0, allow_inf_nan=False)
    h: float = Field(ge=0, allow_inf_nan=False)


class WebViewportIn(BaseModel):
    """选区当时视口的大小，和 `WebRectIn` 一起还原出位置。"""

    model_config = ConfigDict(extra="forbid")

    w: float = Field(gt=0, allow_inf_nan=False)
    h: float = Field(gt=0, allow_inf_nan=False)


class WebElementQuoteIn(BaseModel):
    """网页里指向的一处元素：选择器定位，标签和文字帮受话人认出来。

    `selector` 是运行时在页面里现场算出的短 CSS 路径，`text` 是那处元素的可见文字
    （截到 500 码点）。`rect`/`viewport` 是它当时在屏幕上的位置，好让受话人知道
    「说的就是这里出现的这一处」，而不是页面上任何同名的一处。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["web-element"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    selector: str = Field(min_length=1, max_length=256)
    tag: str = Field(default="", max_length=32)
    text: str = Field(default="", max_length=500)
    rect: WebRectIn
    viewport: WebViewportIn


class WebTextQuoteIn(BaseModel):
    """网页里选中一段文字：形状同 `WebElementQuoteIn`，另带两侧的文字。

    `prefix`/`suffix` 是选中那段前后各 32 码点，用来说清同一句话在页面上出现的
    是哪一处；上限放到 64 是容另一种归一化，同时挡住塞进提示词的长串。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["web-text"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    selector: str = Field(min_length=1, max_length=256)
    tag: str = Field(default="", max_length=32)
    text: str = Field(min_length=1, max_length=500)
    prefix: str = Field(default="", max_length=64)
    suffix: str = Field(default="", max_length=64)
    rect: WebRectIn
    viewport: WebViewportIn


class WebRegionQuoteIn(BaseModel):
    """网页上框选的一块区域：只记网址和位置，不记页面内容。

    这类引用来自没有注入运行时的网页应用（隧道托管的 `cheese serve`），宿主读不到
    里面的元素和文字，能说的只有「在这张网页的这个位置框了一块」。所以它不带文件
    身份（`path`/`source`/`version`）：应用预览本就没有版本可言，网址就是它的身份。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["web-region"]
    url: str = Field(min_length=1)
    rect: WebRectIn
    viewport: WebViewportIn


QuotedContextIn = Annotated[
    SlidePageQuoteIn
    | PagePinQuoteIn
    | SheetCellQuoteIn
    | TextRangeQuoteIn
    | WebElementQuoteIn
    | WebTextQuoteIn
    | WebRegionQuoteIn,
    Field(discriminator="kind"),
]


class ChatAttachmentIn(BaseModel):
    """A file uploaded beforehand, by path."""

    path: str = Field(min_length=1)
    mime: str = ""


class ChatMessageIn(BaseModel):
    content: str = Field(default="", max_length=100000)
    request_id: uuid.UUID
    reply_to: uuid.UUID | None = None
    attachments: list[ChatAttachmentIn] = Field(default_factory=list)
    quoted_context: QuotedContextIn | None = None

    @model_validator(mode="after")
    def quote_is_bounded_message_data(self):
        if self.quoted_context is not None:
            if not self.content.strip():
                raise ValueError(say("quoteNeedsMessage"))
            strings = self.quoted_context.model_dump(mode="json").values()
            if (
                len(self.content) + sum(len(v) for v in strings if isinstance(v, str))
                > 100000
            ):
                raise ValueError(say("quoteTooLong", limit=100000))
        return self
