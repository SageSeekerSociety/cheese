"""What a project's tile may show: the rules a chosen avatar is held to.

The default tile (no kind) is drawn by the frontend from the project's name and
id (`frontend/src/utils/avatar.ts`), so nothing here derives it. What is checked
here is what a person types or picks:

- an icon is named the way the icon font names one (`school`, `flask-outline`);
- an emoji is one grapheme cluster holding a pictograph (a flag, a keycap and a
  skin tone count as one);
- text is letters and digits that fit the tile: at most two CJK characters or
  four others, a CJK character taking the room of two;
- an image is a picture uploaded through `POST /avatars`;
- a hue picks the colour, 0–359 on the avatar palette's wheel.
"""

from __future__ import annotations

from dataclasses import dataclass

import regex

from app.domain.project.models import Project, ProjectAvatarKind

#: How much room the tile has for text, counted in Latin letters.
TEXT_ROOM = 4

_ICON = regex.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PICTOGRAPH = regex.compile(r"\p{Extended_Pictographic}|\p{Regional_Indicator}|⃣")
_WIDE = regex.compile(
    r"[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]"
)
_GRAPHEME = regex.compile(r"\X")


class AvatarRefused(ValueError):
    """The avatar asked for breaks one of the rules above; ``reason`` says which."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AvatarChoice:
    kind: ProjectAvatarKind | None
    glyph: str | None
    hue: int | None
    image_id: int | None


def text_room(text: str) -> int:
    """The room ``text`` takes on the tile: a CJK character two, anything else one."""
    return sum(2 if _WIDE.match(ch) else 1 for ch in text)


def checked(
    kind: ProjectAvatarKind | None,
    glyph: str | None,
    hue: int | None,
    image_id: int | None,
) -> AvatarChoice:
    """The avatar as it will be stored, or ``AvatarRefused``."""
    if hue is not None and not 0 <= hue <= 359:
        raise AvatarRefused("hue")
    if kind is None:
        return AvatarChoice(None, None, hue, None)
    if kind is ProjectAvatarKind.image:
        if image_id is None:
            raise AvatarRefused("image")
        return AvatarChoice(kind, None, hue, image_id)
    value = (glyph or "").strip()
    if kind is ProjectAvatarKind.icon:
        if len(value) > 64 or not _ICON.fullmatch(value):
            raise AvatarRefused("icon")
    elif kind is ProjectAvatarKind.emoji:
        if not _GRAPHEME.fullmatch(value) or not _PICTOGRAPH.search(value):
            raise AvatarRefused("emoji")
    else:
        if (
            not value
            or not all(ch.isalnum() for ch in value)
            or text_room(value) > TEXT_ROOM
        ):
            raise AvatarRefused("text")
    return AvatarChoice(kind, value, hue, None)


def apply(project: Project, choice: AvatarChoice) -> None:
    project.avatar_kind = choice.kind.value if choice.kind is not None else None
    project.avatar_glyph = choice.glyph
    project.avatar_hue = choice.hue
    project.avatar_image_id = choice.image_id
