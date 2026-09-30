"""The words the team domain speaks, parsed for whoever asks.

`ApplicationStatus` is the domain's vocabulary, and a route module may not
import a domain's models directly (C2), so the `?status=` filter of the
`/users/me/team*` lists is parsed here, at home: the route asks the domain
for one of its own words instead of reaching for the model behind it.
"""

from app.core.errors import BadRequestError
from app.domain.team.models import ApplicationStatus


def parse_application_status(value: str | None) -> ApplicationStatus | None:
    """The `?status=` filter of the six `/users/me/team*` lists, or None."""
    if value is None:
        return None
    upper = value.upper()
    if upper in {
        "PENDING",
        "APPROVED",
        "REJECTED",
        "ACCEPTED",
        "DECLINED",
        "CANCELED",
    }:
        return ApplicationStatus[upper]
    raise BadRequestError(f"Invalid status: {value}")
