"""The legal documents a person agrees to, and every version of each (#1486).

The text of each version lives in ``texts/<document>/<version>.md`` and is
served from here to the public pages, so the page someone read and the text a
consent row points at can never be two different files.

Old versions stay: a consent row names the version it accepted, and proving
what that person agreed to means being able to show that text. Publishing a
new version means adding a file and appending an entry — never editing an
existing file, whose hash is already recorded against people's consent.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

_TEXTS = Path(__file__).parent / "texts"


@dataclass(frozen=True)
class LegalVersion:
    version: str
    effective_date: date
    #: A material change (个保法第 14 条第 2 款): everyone who accepted an
    #: earlier version has to accept this one before continuing. A wording fix
    #: that changes nothing a person agreed to is published with False.
    material: bool = True


@dataclass(frozen=True)
class LegalDocument:
    key: str
    title: str
    #: Oldest first; the last one is current.
    versions: tuple[LegalVersion, ...]

    @property
    def current(self) -> LegalVersion:
        return self.versions[-1]

    def get(self, version: str) -> LegalVersion | None:
        return next((v for v in self.versions if v.version == version), None)

    def satisfies(self, accepted_versions: set[str]) -> bool:
        """Whether having accepted these versions covers the current rules:
        some accepted version is at or after the latest material one."""
        required = max(
            (i for i, v in enumerate(self.versions) if v.material), default=0
        )
        return any(v.version in accepted_versions for v in self.versions[required:])


DOCUMENTS: dict[str, LegalDocument] = {
    "terms": LegalDocument(
        key="terms",
        title="用户协议",
        versions=(LegalVersion("1.0", date(2026, 9, 23)),),
    ),
    "privacy": LegalDocument(
        key="privacy",
        title="隐私政策",
        versions=(LegalVersion("1.0", date(2026, 9, 23)),),
    ),
}


@cache
def text_of(document: str, version: str) -> str:
    return (_TEXTS / document / f"{version}.md").read_text(encoding="utf-8")


def sha256_of(document: str, version: str) -> str:
    return hashlib.sha256(text_of(document, version).encode("utf-8")).hexdigest()


def check_current(accepted: dict[str, str]) -> None:
    """Raise ValueError unless ``accepted`` names the current version of every
    document. Account creation requires exactly this: a signup page opened
    before a new version was published must not record consent to the old one.
    """
    if set(accepted) - set(DOCUMENTS):
        raise ValueError("CONSENT_REQUIRED")
    for key, doc in DOCUMENTS.items():
        if key not in accepted:
            raise ValueError("CONSENT_REQUIRED")
        if accepted[key] != doc.current.version:
            raise ValueError("CONSENT_STALE")
