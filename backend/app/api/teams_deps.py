"""What the team-facing routes and the personal `/users/me/team*` ones share.

Three names, and each has two callers or a caller outside this pair.

`get_team_service` is the dependency a route declares when it needs the team
service: `routes/teams.py` mounts it on its own routes, `routes/recruitment.py`
on the plaza's.

`_user_payload` and `application_to_api_model` are the JSON a membership
application answers with, and `load_application_maps` is the bulk load that
keeps a list of them O(1) per row. `routes/users_team.py` lists the same
applications for the signed-in user, so the shape has to be reachable from
there too — and a route module does not import another one (C4
`routes-independent`), which leaves this file as its one home.

`_user_payload` is private and imported back by `routes/teams.py` on purpose:
it builds the `user` object `application_to_api_model` embeds three times, and
the member and team rows that stayed there embed it as well. Splitting it from
its callers would mean passing the profile maps around for nothing.

No router here and nothing to mount: the names below are imported by the
modules that do mount one.
"""

from fastapi import Depends

from app.db.session import get_db
from app.domain.team.services import TeamService, team_service
from app.domain.team.summary import team_summary
from app.domain.user.repositories import UserProfileRepository, UserRepository


async def get_team_service(db=Depends(get_db)) -> TeamService:
    return team_service(db)


def _user_payload(
    user, profile, *, fallback_id: int, avatar_id: int | None = None
) -> dict:
    """Build a User-shaped dict matching frontend `User` type expectations.

    ``avatar_id`` 传的是**这个人自己挑过**的那张（``chosen_avatar_ids`` 的答案，没
    挑过是 None），不从 ``profile.avatar_id`` 取：那一列每个注册路径都写死了全局
    默认头像，照原样回会让所有没挑过头像的人共用同一张脸 —— 而区分人正是头像的活。
    调用方从 ``profiles_map`` 旁边那份 ``avatars_map`` 里拿。
    """
    if user is None:
        return {
            "id": fallback_id,
            "username": "",
            "nickname": "",
            "avatarId": None,
            "intro": "",
            "question_count": 0,
            "answer_count": 0,
        }
    nickname = (
        profile.nickname
        if profile and getattr(profile, "nickname", None)
        else user.username
    )
    return {
        "id": user.id,
        "username": user.username,
        "nickname": nickname,
        "avatarId": avatar_id,
        "intro": profile.intro if profile else "",
        "question_count": 0,
        "answer_count": 0,
    }


def application_to_api_model(
    app,
    *,
    users_map: dict | None = None,
    profiles_map: dict | None = None,
    avatars_map: dict | None = None,
    teams_map: dict | None = None,
) -> dict:
    """TeamMembershipApplication payload aligned with the frontend type.

    Public because the `/users/me/team*` routes in `routes/users_team.py` list
    the same rows for the signed-in user; this is the one home of the shape.

    The frontend ``TeamMembershipApplication`` (cheese-frontend types/teams.ts)
    embeds full ``user``, ``team``, ``initiator`` and optional ``processedBy``
    objects. Bare-id responses caused Members.vue's "已发送邀请" tab to render
    blank rows. Pass bulk-loaded users/profiles/avatars/teams maps to keep this
    O(1) per row in list endpoints.
    """
    users_map = users_map or {}
    profiles_map = profiles_map or {}
    avatars_map = avatars_map or {}
    teams_map = teams_map or {}

    payload: dict = {
        "id": app.id,
        "userId": app.user_id,
        "teamId": app.team_id,
        "user": _user_payload(
            users_map.get(app.user_id),
            profiles_map.get(app.user_id),
            fallback_id=app.user_id,
            avatar_id=avatars_map.get(app.user_id),
        ),
        "team": team_summary(teams_map.get(app.team_id), fallback_id=app.team_id),
        "initiator": _user_payload(
            users_map.get(app.initiator_id),
            profiles_map.get(app.initiator_id),
            fallback_id=app.initiator_id,
            avatar_id=avatars_map.get(app.initiator_id),
        ),
        "type": app.type,
        "status": app.status,
        "role": app.role,
        "message": app.message,
        "processedAt": int(app.processed_at.timestamp() * 1000)
        if app.processed_at
        else None,
        "createdAt": int(app.created_at.timestamp() * 1000) if app.created_at else None,
        "updatedAt": int(app.updated_at.timestamp() * 1000) if app.updated_at else None,
    }
    if app.processed_by_id is not None:
        payload["processedBy"] = _user_payload(
            users_map.get(app.processed_by_id),
            profiles_map.get(app.processed_by_id),
            fallback_id=app.processed_by_id,
            avatar_id=avatars_map.get(app.processed_by_id),
        )
    else:
        payload["processedBy"] = None
    return payload


async def load_application_maps(db, apps) -> tuple[dict, dict, dict, dict]:
    """Bulk-fetch User+UserProfile+chosen-avatar+Team for a batch of applications.

    Public for the same reason as `application_to_api_model` above.
    """
    if not apps:
        return {}, {}, {}, {}
    user_ids = set()
    team_ids = set()
    for app in apps:
        user_ids.add(app.user_id)
        user_ids.add(app.initiator_id)
        if app.processed_by_id is not None:
            user_ids.add(app.processed_by_id)
        team_ids.add(app.team_id)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    service = team_service(db)
    users_map = await user_repo.get_by_ids(list(user_ids))
    profiles_map = await profile_repo.get_profiles_by_user_ids(list(user_ids))
    avatars_map = await profile_repo.chosen_avatar_ids(list(user_ids))
    teams_map = await service.get_teams_by_ids(list(team_ids))
    return users_map, profiles_map, avatars_map, teams_map
