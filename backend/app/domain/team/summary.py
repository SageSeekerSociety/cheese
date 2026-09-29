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


def team_summary_seen_by(
    team: Team | None, *, fallback_id: int, viewer_team_ids: set[int]
) -> dict:
    """``team_summary`` as one viewer may see it.

    The two terms are the team's own rule — :attr:`Team.open_to_all`, and the
    viewer being in it — exactly what ``TeamService.visible_team`` asks. A team
    the viewer is outside and that is not open to all is not named: name,
    handle, intro and avatar go blank. The id stays so the payload keeps its
    shape, and it names nothing on its own, since ``GET /teams/{id}`` answers
    404 to that same viewer.

    Callers that have already authorised the viewer for this team (the team's
    own pages, the post's creator editing it) should use :func:`team_summary`.
    """
    if team is None:
        return team_summary(None, fallback_id=fallback_id)
    if team.open_to_all or team.id in viewer_team_ids:
        return team_summary(team, fallback_id=fallback_id)
    return {
        "id": team.id,
        "handle": None,
        "name": "",
        "intro": "",
        "avatarId": None,
    }
