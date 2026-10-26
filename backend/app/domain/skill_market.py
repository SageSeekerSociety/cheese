"""What the two vendors publish, read live for the skills dialog's market.

Both vendors publish a market the same way, and this module reads it the way
their own clients do: **a manifest file on a git ref**, never a directory walk.
Claude Code's ``/plugin marketplace add`` and Codex's ``plugin marketplace add``
both fetch a repository and read its catalogue file; that is why neither client
has an hour-of-60 problem, and this is what following them buys us.

* **skills** — Anthropic's ``anthropics/skills`` manifest names its skill folders
  outright (``skills: ["./skills/pdf", ...]``). OpenAI has no skill catalogue any
  more: its README retires ``openai/skills`` in favour of ``openai/plugins``,
  where a skill is a folder inside a plugin — so OpenAI's skills arrive through
  the plugin catalogue below, not as skill cards.
* **plugins** — each vendor's own plugin catalogue: Anthropic's
  ``claude-plugins-official`` and OpenAI's ``openai/plugins``. A card's address
  is the folder to install from, and it carries the manifest's pinned ``sha``
  when it names one — that commit is the reviewed one, and installing the branch
  instead would quietly install something nobody approved.
* **mcp** — the official MCP registry, where an MCP server — what Claude calls a
  *connector* — is published. Read-only: a server is not a skill, so there is
  nothing here to land on a machine.

Everything is read over ``api.github.com`` and the registry, never
``raw.githubusercontent.com`` (unreachable on this network; measured
2026-10-11), and never a repository archive (throttled to about 25 KB/s here:
4 MB took 179 seconds, where the same files took under a second each through the
contents API). One source failing costs its own list and a line in ``notes``,
never the whole market: a catalogue being down is no reason for the dialog to
lose the other two.

A read that never got an answer is asked again before it is given up on: this
network drops a connection now and then, and a dropped one says nothing about
the vendor. A reading that lost a shelf that way is then held only briefly, so
the next open asks again instead of showing the same hole for half an hour.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.domain.project_skill.importer import (
    github_headers,
    parse_github_url,
    parse_skill_md,
)

API = "https://api.github.com"
MCP_REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"

#: Anthropic's skill catalogue: the manifest that lists the skill folders it
#: publishes, and the name a card badge carries.
SKILLS = {
    "source": "anthropic",
    "owner": "anthropics",
    "repo": "skills",
    "manifest": ".claude-plugin/marketplace.json",
}

#: Each vendor's plugin catalogue. ``plugin_manifest`` is where a plugin of that
#: catalogue keeps its own metadata, read only when the catalogue entry did not
#: carry a description itself (Anthropic's does; OpenAI's names the plugin and
#: nothing more).
PLUGIN_SOURCES = (
    {
        "source": "anthropic",
        "owner": "anthropics",
        "repo": "claude-plugins-official",
        "manifest": ".claude-plugin/marketplace.json",
        "plugin_manifest": ".claude-plugin/plugin.json",
    },
    {
        "source": "openai",
        "owner": "openai",
        "repo": "plugins",
        "manifest": ".agents/plugins/marketplace.json",
        "plugin_manifest": ".codex-plugin/plugin.json",
    },
)

#: How many cards a screen gets. Anthropic's plugin catalogue alone names 315 and
#: OpenAI's 65; a wall of them is not a market, it is a directory listing. The
#: count travels beside it so the note can say what was left out.
PLUGIN_LIMIT = 24
MCP_LIMIT = 24

#: A catalogue read is a small file; if it trickles, give up rather than hold the
#: dialog open. Both hosts answer one in well under a second when healthy.
READ_TIMEOUT_S = 10

#: How many times one file is asked for before the market gives up on it, and
#: how long the wait grows between those asks. This network drops a connection
#: now and then — measured 2026-10-26, a quarter to two fifths of fresh
#: connections to ``api.github.com`` and the MCP registry were reset — and one
#: attempt each let a single drop cost a whole shelf.
READ_ATTEMPTS = 3
READ_BACKOFF_S = 0.4

#: Answers worth asking for again: the host answered, but not with the file.
#: A 403 (the hour's requests are gone) or a 404 (not there) is itself a fact;
#: asking again only spends what requests are left.
RETRY_STATUSES = frozenset({500, 502, 503, 504})
#: How many cards of one catalogue are described when no token is configured,
#: and how many reads are in flight at once. Every description is a request of
#: its own and the unauthenticated GitHub API allows 60 an hour — the same 60
#: that pays for the person's own 「install」 clicks right after they browse. A
#: market is not worth emptying the hour for; a card past the budget shows its
#: name and vendor, which is still a card. With a token the hour holds 5000 and
#: every card shown is described.
DESCRIBE_LIMIT = 12
DESCRIBE_CONCURRENCY = 6

#: ``ponytail:`` one process, dropped on restart, not shared between workers.
#: Upgrade path: a shared cache.
CACHE_SECONDS = 1800
#: A read that lost a shelf to the network is held only this long, so the next
#: open asks again instead of showing the same hole for half an hour. A refusal
#: or a missing file is a real answer and keeps the full ``CACHE_SECONDS``.
RETRY_SOON_SECONDS = 60
_CACHE: dict[str, tuple[float, Any, float]] = {}

#: ``(owner, repo, ref, path)`` — where to read one card's own file from.
Where = Callable[[dict], "tuple[str, str, str, str] | None"]


async def _cached(key: str, build: Any) -> Any:
    """``build``'s value, held for the time it asked to be held."""
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < hit[2]:
        return hit[1]
    value, ttl = await build()
    _CACHE[key] = (time.monotonic(), value, ttl)
    return value


class _Unanswered(LookupError):
    """The connection ended this read, not the vendor: nothing ever answered.

    Distinct from 「not there」 and from a refusal because asking again in a
    moment may well get an answer, and only this kind is worth re-reading soon
    (``RETRY_SOON_SECONDS``).
    """


class _Notes:
    """What a read has to say about itself.

    ``lines`` is what the dialog shows under the shelves; ``unanswered`` is
    whether any shelf was lost to the network rather than to an answer.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.unanswered = False


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response | None:
    """One GET, asked again while the failure is the connection's or the host's.

    ``None`` when every attempt went unanswered, so a caller says 「no answer」
    rather than making a status up. An answer a caller can act on — refused,
    not there — comes back as it is, unasked.
    """
    got: httpx.Response | None = None
    for attempt in range(READ_ATTEMPTS):
        try:
            got = await client.get(
                url, params=params, headers=headers, timeout=READ_TIMEOUT_S
            )
        except httpx.TransportError:
            got = None
        if got is not None and got.status_code not in RETRY_STATUSES:
            return got
        if attempt + 1 < READ_ATTEMPTS:
            await asyncio.sleep(READ_BACKOFF_S * 2**attempt)
    return got


async def _read_file(
    client: httpx.AsyncClient, owner: str, repo: str, ref: str, path: str
) -> tuple[int, bytes | None]:
    """One file over the contents API, with the status that answered for it.

    Not ``importer.fetch_file``: that answers ``None`` for every failure because
    the importer treats 「not there」 as a fact a caller may act on. A catalogue
    has to tell the difference — a 403 is the hour's requests being gone, and
    reporting it as 「没有这个文件」 sends the reader looking for a file that is
    there. ``0`` means the request never got an answer at all.
    """
    got = await _get(
        client,
        f"{API}/repos/{owner}/{repo}/contents/{quote(path)}",
        params={"ref": ref},
        headers=github_headers(),
    )
    if got is None:
        return 0, None
    if got.status_code != 200:
        return got.status_code, None
    payload = got.json()
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        return got.status_code, b""  # an empty file comes back without a payload
    try:
        return got.status_code, base64.b64decode(payload.get("content") or "")
    except ValueError:
        return got.status_code, None


def _why(status: int) -> str:
    """Why a read came back without a body, said so the note is worth reading."""
    if status in (403, 429):
        return "GitHub refused it (rate limit — set GITHUB_TOKEN to raise it)"
    if status == 404:
        return "not there"
    if not status:
        return "no answer"
    return f"answered {status}"


async def _manifest(client: httpx.AsyncClient, source: dict) -> dict:
    """The catalogue file a vendor publishes, as the JSON it holds."""
    status, raw = await _read_file(
        client, source["owner"], source["repo"], "main", source["manifest"]
    )
    where = f"{source['owner']}/{source['repo']} {source['manifest']}"
    if raw is None:
        # i18n-exempt: 对方那份目录文件怎么了，排错用的；没有人接这个 LookupError
        message = f"{where}: {_why(status)}"
        if not status:
            raise _Unanswered(message)
        raise LookupError(message)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # i18n-exempt: 同上 —— 对方发布的东西不是 JSON，这句话是给排错的人看的。
        raise LookupError(f"{where}: not JSON") from None
    if not isinstance(document, dict):
        # i18n-exempt: 同上 —— 对方发布的东西不是一份对象。
        raise LookupError(f"{where}: not an object")
    return document


def _folder_of(url: str) -> str:
    """The repository path a ``/tree/<ref>/<path>`` link names, or ``""``."""
    if "/tree/" not in url:
        return ""
    tail = url.split("/tree/", 1)[1]
    return tail.split("/", 1)[1] if "/" in tail else ""


def _frontmatter_description(raw: bytes) -> str:
    """The description a SKILL.md advertises, or ``""`` when it will not read."""
    try:
        return parse_skill_md(raw.decode("utf-8")).description
    except (UnprocessableEntityError, UnicodeDecodeError):
        return ""


def _plugin_description(raw: bytes) -> str:
    """The description a plugin's own manifest carries, or ``""``."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(document, dict):
        return ""
    return str(document.get("description") or "")


def _describe_budget() -> int | None:
    """How many cards of one catalogue a run describes, ``None`` for all of them.

    With a token the hour holds 5000 requests and every card shown can have its
    own line; without one it holds 60, shared with the person's own installs.
    """
    return None if settings.github_token.strip() else DESCRIBE_LIMIT


async def _describe(
    client: httpx.AsyncClient,
    cards: list[dict],
    notes: _Notes,
    *,
    where: Where,
    parse: Callable[[bytes], str],
    label: str,
) -> None:
    """Fill cards' ``description`` from a file of their own, in place.

    Read tolerantly: a market card is no place to enforce a spec, and one odd
    file must not empty the listing.
    """
    budget = _describe_budget()
    todo = [card for card in cards if not card["description"] and card["url"]]
    todo = todo if budget is None else todo[:budget]
    if not todo:
        return
    gate = asyncio.Semaphore(DESCRIBE_CONCURRENCY)
    stopped = asyncio.Event()

    async def one(card: dict) -> None:
        at = where(card)
        if at is None or stopped.is_set():
            return
        owner, repo, ref, path = at
        async with gate:
            if stopped.is_set():
                return
            status, raw = await _read_file(client, owner, repo, ref, path)
        if status in (403, 429):
            stopped.set()  # the hour's requests are gone; stop spending them
            return
        if raw is None:
            return
        card["description"] = parse(raw)

    await asyncio.gather(*(one(card) for card in todo))
    if stopped.is_set():
        notes.lines.append(f"{label}: descriptions stopped early (GitHub rate limit)")


async def _skill_cards(client: httpx.AsyncClient, notes: _Notes) -> list[dict]:
    """Anthropic's skills as cards, described as far as the budget reaches."""
    document = await _manifest(client, SKILLS)
    entries = [e for e in (document.get("plugins") or []) if isinstance(e, dict)]
    folders = [
        folder
        for entry in entries
        for folder in (
            str(declared).strip("./") for declared in (entry.get("skills") or [])
        )
        if folder
    ]
    cards = [
        {
            "name": folder.rsplit("/", 1)[-1],
            "description": "",
            "url": (
                f"https://github.com/{SKILLS['owner']}/{SKILLS['repo']}"
                f"/tree/main/{folder}"
            ),
            "source": SKILLS["source"],
            "kind": "skill",
        }
        for folder in dict.fromkeys(folders)  # the manifest's own order, once each
    ]
    await _describe(
        client,
        cards,
        notes,
        where=lambda card: (
            SKILLS["owner"],
            SKILLS["repo"],
            "main",
            f"{_folder_of(card['url'])}/SKILL.md",
        ),
        parse=_frontmatter_description,
        label=f"{SKILLS['source']} skills",
    )
    return cards


def _plugin_folder(source: object, owner: str, repo: str) -> str:
    """A plugin's GitHub folder address, or ``""`` when it has none.

    The manifests write ``source`` several ways: a string path inside the
    catalogue's own repository (``./plugins/<x>`` or ``./external_plugins/<x>``),
    an object naming such a path (``{"source": "local", "path": ...}``, Codex's
    spelling), or an object naming another repository and a folder in it. An npm
    package has no folder to read, so its card is listed without an install.

    A pinned ``sha`` wins over ``ref``: both catalogues treat it as the reviewed
    commit, so reading ``main`` instead would install something nobody approved.
    """
    if isinstance(source, str):
        path = source.strip("./")
        return f"https://github.com/{owner}/{repo}/tree/main/{path}" if path else ""
    if not isinstance(source, dict):
        return ""
    ref = str(source.get("sha") or source.get("ref") or "main")
    path = str(source.get("path") or "").strip("./")
    kind = str(source.get("source") or "")
    if kind == "local" or (not kind and path):
        return f"https://github.com/{owner}/{repo}/tree/main/{path}" if path else ""
    url = str(source.get("url") or "")
    if "github.com/" not in url:
        return ""
    parts = [part for part in url.split("github.com/", 1)[1].split("/") if part]
    if len(parts) < 2:
        return ""
    where = str(source.get("path") or "").strip("/")
    tail = f"/{where}" if where else ""
    return (
        f"https://github.com/{parts[0]}/{parts[1].removesuffix('.git')}"
        f"/tree/{ref}{tail}"
    )


async def _plugin_cards(
    client: httpx.AsyncClient, source: dict, notes: _Notes
) -> tuple[list[dict], int]:
    """One vendor's plugins as cards, and how many its catalogue names."""
    document = await _manifest(client, source)
    entries = [
        entry
        for entry in (document.get("plugins") or [])
        if isinstance(entry, dict) and entry.get("name")
    ]
    cards = [
        {
            "name": str(entry["name"]),
            "description": str(entry.get("description") or ""),
            "url": _plugin_folder(entry.get("source"), source["owner"], source["repo"]),
            "source": source["source"],
            "kind": "plugin",
        }
        for entry in entries[:PLUGIN_LIMIT]
    ]

    def where(card: dict) -> tuple[str, str, str, str] | None:
        if not card["url"]:
            return None
        place = parse_github_url(card["url"])
        folder = place.path.strip("/")
        manifest = (
            f"{folder}/{source['plugin_manifest']}"
            if folder
            else source["plugin_manifest"]
        )
        return place.owner, place.repo, place.ref, manifest

    await _describe(
        client,
        cards,
        notes,
        where=where,
        parse=_plugin_description,
        label=f"{source['source']} plugins",
    )
    return cards, len(entries)


async def _mcp_cards(client: httpx.AsyncClient) -> list[dict]:
    """The official registry's newest servers — read-only, nothing to install.

    ``version=latest`` is not decoration: the registry lists **every published
    version**, so a plain ``limit=24`` came back as 15 servers with one repeated
    four times (measured 2026-10-11). The parameter makes the registry answer
    with one entry per server.
    """
    got = await _get(
        client, MCP_REGISTRY, params={"limit": MCP_LIMIT, "version": "latest"}
    )
    if got is None:
        # i18n-exempt: 注册表一直没应答 —— 排错用的，不是给读者的一句话。
        raise _Unanswered("no answer from the MCP registry")
    if got.status_code != 200:
        # i18n-exempt: 官方注册表回了一个非 200 —— 排错用的数字，不是给读者的一句话。
        raise LookupError(f"the MCP registry answered {got.status_code}")
    rows = got.json().get("servers") or []
    return [
        {
            "name": str(
                (row.get("server") or {}).get("title")
                or (row.get("server") or {}).get("name")
                or ""
            ),
            "description": str((row.get("server") or {}).get("description") or ""),
            "url": "",
            "source": "mcp",
            "kind": "mcp",
        }
        for row in rows
        if isinstance(row, dict) and row.get("server")
    ]


async def _guard(work: Awaitable[Any], notes: _Notes, label: str) -> Any | None:
    """``work``, or ``None`` with the reason in ``notes`` — one source's loss."""
    try:
        return await work
    except Exception as exc:  # a catalogue being down must not empty the market
        # A timeout's message can be empty; its type is the only thing left to say.
        notes.lines.append(f"{label}: {str(exc) or type(exc).__name__}")
        if isinstance(exc, (_Unanswered, httpx.TransportError)):
            notes.unanswered = True
        return None


async def _read(client: httpx.AsyncClient) -> tuple[dict[str, Any], float]:
    """The three lists, and how long this reading may be held.

    A shelf lost to the network is held only ``RETRY_SOON_SECONDS``: the next
    open asks again, so a drop cannot blank the market for half an hour. What
    the vendors did answer — including a refusal — is held the full time.
    """
    notes = _Notes()
    skills, plugins, mcp = await asyncio.gather(
        _guard(_skill_cards(client, notes), notes, "skills"),
        asyncio.gather(
            *(
                _guard(
                    _plugin_cards(client, source, notes),
                    notes,
                    f"{source['source']} plugins",
                )
                for source in PLUGIN_SOURCES
            )
        ),
        _guard(_mcp_cards(client), notes, "mcp"),
    )
    listed = skills or []
    plugin_cards = [card for group in plugins if group for card in group[0]]
    payload = {
        "skills": listed,
        "plugins": plugin_cards,
        "plugin_total": sum(group[1] for group in plugins if group),
        "mcp": mcp or [],
        "notes": notes.lines,
    }
    return payload, RETRY_SOON_SECONDS if notes.unanswered else CACHE_SECONDS


async def catalogue(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """The market's three lists, read live and cached for a while."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        return await _cached("catalogue", lambda: _read(client))
    finally:
        if owns:
            await client.aclose()
