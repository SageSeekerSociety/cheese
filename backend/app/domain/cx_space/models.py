"""Space model — 机构 (书院 / 信院 / eTrip), spec §4.1.

A Space does ONE thing: publish Task Templates. It does not manage Projects
directly and sets no governance policy. The Space board (spec §7.3) aggregates
all projects that linked a Task under this Space.
"""

import enum

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class SpaceKind(enum.StrEnum):
    college = "college"  # 书院
    school = "school"  # 学院/信院
    company = "company"  # 企业
    other = "other"


class Space(UuidPk, Timestamps, Base):
    __tablename__ = "spaces"

    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[SpaceKind] = mapped_column(
        Enum(SpaceKind, native_enum=False, length=16), default=SpaceKind.other
    )
    description: Mapped[str] = mapped_column(Text, default="")
