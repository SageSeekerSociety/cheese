"""Wire shape of a resolved 壳 (see `catalog.py` for what one is)."""

from pydantic import BaseModel

from app.domain.shell.catalog import Nav, Shell


class ShellNavOut(BaseModel):
    """The three surfaces' cells, in order. See `catalog.Nav`."""

    rail: list[str]
    tabs: list[str]
    project: list[str]


class ShellOut(BaseModel):
    """A 壳 as the frontend receives it: data only, nothing to interpret.

    This is the RESOLVED declaration, not the name that was declared. The
    frontend must not own a second copy of the four built-in 壳 — a shell added
    to the backend catalog has to reach the browser without a frontend release.
    """

    name: str
    home: str | None
    nav: ShellNavOut
    hidden: list[str]
    terms: dict[str, str]

    @classmethod
    def of(cls, shell: Shell) -> "ShellOut":
        nav: Nav = shell.nav
        return cls(
            name=shell.name,
            home=shell.home,
            nav=ShellNavOut(
                rail=list(nav.rail), tabs=list(nav.tabs), project=list(nav.project)
            ),
            hidden=list(shell.hidden),
            terms=dict(shell.terms),
        )
