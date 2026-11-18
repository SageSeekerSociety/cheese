"""The author card both the answer list and the question list put on a row.

The fields are a contract (`docs/design-system.md` §3.14). One definition means
a field added for one list cannot go missing from the other without a test
saying so.

``chosen_avatar_id`` comes from ``UserProfileRepository.chosen_avatar_ids``:
the profile's own ``avatar_id`` carries the shared default written at
registration, so returning it would give everyone who never picked one the same
face.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.models import UserProfile
from app.domain.user.repositories import UserRepository
from app.domain.user.services import remarks_for_viewer, usernames_by_ids


async def page_names(
    session: AsyncSession | None, viewer_id: int | None, user_ids: list[int]
) -> dict[int, dict]:
    """user id -> ``{"username", "remark"}`` for one page, one query each.

    Every board that puts a person on a row needs the same three things: who
    they are (the account, which seeds the avatar colour), what they are called
    publicly (the profile's nickname, which the card already carries), and what
    **this reader** privately calls them. Only the last is per-reader, and it is
    ``None`` when nobody is reading — an agent's read, an export, an email —
    in which case the page shows the public nickname.

    Lives beside ``profile_to_author`` so a board cannot add one half of the
    card and forget the other.
    """
    if session is None or not user_ids:
        return {}
    handles = await usernames_by_ids(session, user_ids)
    if viewer_id is None:
        return {uid: {"username": h, "remark": None} for uid, h in handles.items()}
    people = await UserRepository(session=session).get_by_ids(user_ids)
    remarks = await remarks_for_viewer(session, viewer_id, people)
    return {
        uid: {"username": h, "remark": remarks.get(h)} for uid, h in handles.items()
    }


def profile_to_author(
    profile: UserProfile | None,
    chosen_avatar_id: int | None = None,
    *,
    username: str | None = None,
    remark: str | None = None,
) -> dict | None:
    """The card for ``profile``, or ``None`` when there is no profile.

    ``remark`` is the **reader's own** private name for this person, read for
    the one page being answered (``domain.user.services.remarks_for_viewer``). It
    is ``None`` when nobody is reading — an agent's read, an export — and the
    page then shows ``nickname``. ``username`` is the account, which is what
    identifies the person on screen and seeds their avatar colour; it is not
    a name and is not swapped.
    """
    if profile is None:
        return None
    return {
        "id": profile.user_id,
        "nickname": profile.nickname,
        "username": username,
        "remark": remark,
        "avatarId": chosen_avatar_id,
        "intro": profile.intro,
    }
