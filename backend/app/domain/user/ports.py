"""Calls ``user`` makes that only a higher layer can answer.

The account dto carries six counts — the teams a person joined, the tasks they
took part in, the knowledge entries, questions and answers they wrote, the
submissions they handed in. Each one is a row count over another domain's
table, and those four packages sit *above* this one in the layer table
(``docs/manual/dev/architecture.md``), so ``app.domain.user`` may not read
them. The counting is done on the other side of that line and reaches the dto
as a value.

That is the whole shape of this port: ``user`` names the one call it needs,
``app.domain.dashboard.user_statistics`` implements it (layer 7, where a query
spanning packages belongs), and the composition root
(``app.api.deps.get_user_auth_service``) hands one to ``UserAuthService``.
"""

from collections.abc import Mapping, Sequence
from typing import Final, Protocol


class UserStatistics(Protocol):
    """The per-person counters the account dto carries."""

    async def aggregate(self, user_id: int) -> dict[str, int]:
        """``teamCount``, ``taskParticipationCount``, ``knowledgeCount``,
        ``submissionCount``, ``questionCount``, ``answerCount`` — zeros, not
        ``None``, for a person with nothing."""
        ...


class BoardLabel(Protocol):
    """How a board is named where something happened on it."""

    #: Read-only: `space.SpaceLabel` is a frozen dataclass, so declaring this
    #: settable would make the port match nothing.
    name: Final[str]


class BoardLabels(Protocol):
    """Names the boards a page of real-name access records happened on.

    The access log names the board each access took place on
    (``accessEntityName``), and naming a board is a query against that board's
    own table — which lives in ``app.domain.space``, above this package. So
    ``user`` names the one call it needs here and the composition root hands
    ``UserRealNameService`` the space-side implementation
    (``app.domain.space.services.SpaceLabels``), whose shape this repeats.
    """

    async def describe(self, space_ids: Sequence[int]) -> Mapping[int, BoardLabel]:
        """Each of these boards' labels, in one query; unknown ids are left
        out."""
        ...
