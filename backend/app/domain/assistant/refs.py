"""What a person's 芝士 calls things by: the address bar's own words.

A thing is named as its page's address is, less ``/projects/``: a channel is
``ml-course/channels/3``, a task ``ml-course/tasks/12``, its review card
``ml-course/tasks/12/review``, a document ``ml-course/docs/5``, the project
itself ``ml-course``. What lives outside projects is named by its kind and id:
``spaces/4``, ``spaces/4/tasks/87`` (a 题目), ``drafts/12``, ``files/30``,
``alerts/55``. The person sees the same words in their address bar; 芝士 never
sees a UUID unless a thing has nothing else (an invitation).

This module only reads and writes the words. What a ref names, and whether
the person may see it, is the route's to find out (`project.address` holds the
numbers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What lives outside a project, by its first segment.
GLOBAL_KINDS = (
    "spaces",
    "teams",
    "drafts",
    "files",
    "alerts",
    "invites",
    "transfers",
    "team-invites",
    "join-requests",
    "runs",
)

_SLUG = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_PROJECT_THING = re.compile(
    rf"(?P<slug>{_SLUG})/(?P<kind>channels|tasks|docs)/#?(?P<number>\d{{1,9}})"
    r"(?P<review>/review)?"
)
_PROJECT = re.compile(rf"(?P<slug>{_SLUG})")
_SPACE_TASK = re.compile(r"spaces/(?P<space>\d{1,9})/tasks/(?P<task>\d{1,9})")
_GLOBAL = re.compile(
    r"(?P<kind>[a-z-]+)/(?P<id>[0-9a-fA-F-]{1,36})(?:/(?P<more>\d{1,9}))?"
)


@dataclass(frozen=True)
class Ref:
    """One parsed ref. ``kind`` is ``project``, ``channel``, ``task``,
    ``review``, ``doc``, ``space``, ``problem`` (a 题目) or one of the global
    kinds in the singular (``draft``, ``file``, ``alert``, ``invite``,
    ``transfer``, ``team-invite``, ``join-request``, ``run``)."""

    kind: str
    #: The project's slug, for a project's own things.
    slug: str | None = None
    #: The number inside the project, or the id outside one.
    number: int | None = None
    id: str | None = None
    #: The second id a ref carries: a 题目's space, a join request's team.
    within: str | None = None


_SINGULAR = {
    "channels": "channel",
    "tasks": "task",
    "docs": "doc",
    "spaces": "space",
    "teams": "team",
    "drafts": "draft",
    "files": "file",
    "alerts": "alert",
    "invites": "invite",
    "transfers": "transfer",
    "team-invites": "team-invite",
    "join-requests": "join-request",
    "runs": "run",
}


def parse(ref: str) -> Ref | None:
    """What ``ref`` says, or None when it is none of the shapes above. Spaces
    around it, a leading ``/``, ``/projects/`` and the site's own origin are
    forgiven: a ref copied from an address still reads."""
    text = ref.strip().strip("[]「」").strip()
    text = re.sub(r"^https?://[^/]+", "", text)
    text = text.removeprefix("/").removeprefix("projects/").rstrip("/")
    if m := _SPACE_TASK.fullmatch(text):
        return Ref("problem", id=m["task"], within=m["space"])
    if m := _PROJECT_THING.fullmatch(text.lower()):
        kind = "review" if m["review"] else _SINGULAR[m["kind"]]
        return Ref(kind, slug=m["slug"], number=int(m["number"]))
    if m := _GLOBAL.fullmatch(text):
        if m["kind"] in GLOBAL_KINDS:
            return Ref(
                _SINGULAR[m["kind"]],
                id=m["more"] or m["id"],
                within=m["id"] if m["more"] else None,
            )
        return None
    if _PROJECT.fullmatch(text.lower()) and text.lower() not in GLOBAL_KINDS:
        return Ref("project", slug=text.lower())
    return None


def project(slug: str) -> str:
    return slug


#: A thing a project has not numbered (made before numbers were) has no
#: address of its own; it reads as its project does.
def channel(slug: str, number: int | None) -> str:
    return f"{slug}/channels/{number}" if number is not None else slug


def task(slug: str, number: int | None) -> str:
    return f"{slug}/tasks/{number}" if number is not None else slug


def review(slug: str, number: int | None) -> str:
    return f"{slug}/tasks/{number}/review" if number is not None else slug


def doc(slug: str, number: int | None) -> str:
    return f"{slug}/docs/{number}" if number is not None else slug


def problem(space_id: int, task_id: int) -> str:
    return f"spaces/{space_id}/tasks/{task_id}"


def of(kind: str, id: object, within: object | None = None) -> str:
    """A global thing's ref: ``of("alerts", 55)`` is ``alerts/55``;
    ``of("join-requests", 9, within=3)`` is ``join-requests/3/9``."""
    return f"{kind}/{within}/{id}" if within is not None else f"{kind}/{id}"
