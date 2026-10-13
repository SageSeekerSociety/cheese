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

from typing import Protocol


class UserStatistics(Protocol):
    """The per-person counters the account dto carries."""

    async def aggregate(self, user_id: int) -> dict[str, int]:
        """``teamCount``, ``taskParticipationCount``, ``knowledgeCount``,
        ``submissionCount``, ``questionCount``, ``answerCount`` — zeros, not
        ``None``, for a person with nothing."""
        ...
