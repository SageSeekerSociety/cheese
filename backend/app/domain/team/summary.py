"""The short form of a team that other payloads embed (TeamSummary)."""

from app.domain.team.models import Team


def team_summary(team: Team | None, *, fallback_id: int) -> dict:
    """``{id, handle, name, intro, avatarId}``; blanks when the team is gone.

    Every place that shows a team inside something else — an application, a
    registration, a recruitment post, a task — builds this one shape, so a field
    the page needs (the handle its links go to) is added once.
    """
    if team is None:
        return {
            "id": fallback_id,
            "handle": None,
            "name": "",
            "intro": "",
            "avatarId": None,
        }
    return {
        "id": team.id,
        "handle": team.handle,
        "name": team.name,
        "intro": team.intro,
        "avatarId": team.avatar_id,
    }
