"""Team values and read interfaces shared with service callers."""

from datetime import datetime
from enum import Enum
from typing import Protocol


class TeamVisibility(str, Enum):
    """Who can find a team without being in it.

    PUBLIC teams show up in search and open by id; STEALTH teams do neither and
    are reached only through the team's join link. How joining then works is
    the team's ``join_approval`` — the same switch a project has.
    """

    PUBLIC = "public"
    STEALTH = "stealth"


class TeamMemberRole:
    OWNER = 0
    ADMIN = 1
    MEMBER = 2


class ApplicationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELED = "CANCELED"


class RecruitmentStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class TeamView(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def handle(self) -> str | None: ...
    @property
    def personal_owner_user_id(self) -> int | None: ...
    @property
    def intro(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def avatar_id(self) -> int: ...
    @property
    def visibility(self) -> str: ...
    @property
    def join_approval(self) -> bool: ...
    @property
    def join_token(self) -> str | None: ...
    @property
    def created_at(self) -> datetime | None: ...
    @property
    def updated_at(self) -> datetime | None: ...


class TeamMemberView(Protocol):
    @property
    def user_id(self) -> int: ...
    @property
    def role(self) -> int: ...
    @property
    def created_at(self) -> datetime | None: ...
    @property
    def updated_at(self) -> datetime | None: ...
