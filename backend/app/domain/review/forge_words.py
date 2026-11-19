"""Whether a sentence about a refused merge names the forge that refused it.

A project bound to GitHub reads "GitHub refused to merge": it is true, and the
reader knows the place the refusal came from. The platform's own forge is not a
product the reader has met by name, so its sentences say only that the merge
was refused. Every sentence of that family is chosen here, from the project's
forge, for people and for the teammate alike.
"""

from __future__ import annotations

from app.core.sentences import NoticeText, say
from app.domain.review.forge import ForgeKind

#: Each sentence of the family: what it says on the platform's own forge, and
#: what it says on GitHub.
_ON_GITHUB = {
    "mergeRefusedPlatform": "mergeRefused",
    "mergeRefusedOnAcceptPlatform": "mergeRefusedOnAccept",
    "labelRefusalReason": "labelGithubReason",
    "labelRefusalReply": "labelGithubReply",
    "prMergeRefused": "githubMergeRefused",
}


def forge_name(kind: ForgeKind | str) -> str | None:
    """The name a sentence gives the forge, or None where it names none."""
    return "GitHub" if kind == ForgeKind.github_app else None


def said(kind: ForgeKind | str, key: str, **params) -> NoticeText:
    """The sentence ``key`` (its platform-forge wording) as the project's
    forge ``kind`` makes it true."""
    return say(_ON_GITHUB[key] if forge_name(kind) else key, **params)
