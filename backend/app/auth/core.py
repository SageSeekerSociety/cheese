from dataclasses import dataclass, field
from enum import Enum


class SystemRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    USER = "USER"
    GUEST = "GUEST"


@dataclass
class AuthUserInfo:
    user_id: int
    system_roles: set[SystemRole] = field(default_factory=lambda: {SystemRole.USER})
