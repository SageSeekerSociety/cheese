"""The author card both the answer list and the question list put on a row.

The fields are a contract (`docs/design-system.md` §3.14). One definition means
a field added for one list cannot go missing from the other without a test
saying so.

``chosen_avatar_id`` comes from ``UserProfileRepository.chosen_avatar_ids``:
the profile's own ``avatar_id`` carries the shared default written at
registration, so returning it would give everyone who never picked one the same
face.
"""

from app.domain.user.models import UserProfile


def profile_to_author(
    profile: UserProfile | None, chosen_avatar_id: int | None = None
) -> dict | None:
    """The card for ``profile``, or ``None`` when there is no profile."""
    if profile is None:
        return None
    return {
        "id": profile.user_id,
        "nickname": profile.nickname,
        "avatarId": chosen_avatar_id,
        "intro": profile.intro,
    }
