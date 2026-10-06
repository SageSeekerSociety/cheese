"""The title of the one commit a topic leaves on the project's default branch.

The platform squash-merges, so a whole topic collapses into a single commit
whose title is the card's subject. Its format and language belong to the
hosted repository: a project keeps its own commit convention, and an agent
follows it by reading that project's recent history and contributing docs
(the `cheese_accept_request` tool says so). A repository that wants its
convention enforced does that in its own tooling — this repo's is
`.github/scripts/check-commit-title.py`.

So the platform checks only what breaks the platform's own use of the line:
it must exist, and it must be one line, because it becomes a PR title and a
commit's first line, and neither can hold a line break.
"""

from app.core.sentences import say


class InvalidSubject(ValueError):
    """Carries the sentence shown to whoever filed the card."""


def check_subject(subject: str) -> str:
    """Return the subject stripped, or raise `InvalidSubject` saying what is
    wrong."""
    subject = subject.strip()
    if not subject:
        raise InvalidSubject(say("commitSubjectEmpty"))
    if "\n" in subject:
        raise InvalidSubject(say("commitSubjectOneLine"))
    return subject


def valid_subject(subject: str | None) -> str | None:
    """Non-raising variant for read paths that only need to know whether a
    stored subject is usable."""
    if not subject:
        return None
    try:
        return check_subject(subject)
    except InvalidSubject:
        return None
