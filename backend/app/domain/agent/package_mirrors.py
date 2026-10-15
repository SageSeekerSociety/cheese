"""Where a room's package downloads come from, and when they come from a mirror.

An agent in a room installs things — `uv add`, `pip install`, `npm i`,
`playwright install` — and every byte of that leaves the box it runs on. From
China the public registries are on the far side of a long path, so a room
fetches from a domestic mirror where one answers, and from the official source
otherwise. None of this reaches an agent's prompt, its skills or its memory:
it is process environment and nothing else.

ONE TABLE, TWO RENDERINGS. The same policy has to hold in two places that
cannot share code: the Python executor, whose environment an agent's tools
inherit (`bootstrap.prepared`), and the launcher script a device runs
(`machine_launcher.launch_script`), which is POSIX shell assembled from
strings. `MIRRORS` below is the one table, `environment` renders it as a
mapping, `shell` renders it as statements, and a test holds the two together.

WHY EVERY MIRROR IS PROBED FIRST. PyPI is the only one of these that admits a
second, lower-priority index — `UV_DEFAULT_INDEX` for uv, `PIP_EXTRA_INDEX_URL`
for pip — so there a missing or not-yet-synced package still resolves, and a
mirror answering 404 falls through by itself. Nothing else does: npm,
`playwright install` and `uv python install` take one address each, and a
mirror that is down there is a room that cannot install anything at all, which
is worse than the traffic it saves. So a mirror is used only once a probe says
it answers; one that does not stays unset, and that tool is on the official
source, exactly where it was before this existed.

THE NAMES AS WELL AS THE VALUES. A room prepares on every tool call it serves,
and the prepare hands its environment to the executor as something to add to
what the room already has (`runtime`, the `configure` request). So a mirror that
stops answering cannot be expressed by leaving a variable out: the room is
already carrying it. `governed` is the other half — every name the deployment
has given an address for — and it travels beside the environment so the
executor can take away the ones this prepare did not set. That is also how a
deployment that turns the feature off gets the old behaviour back in rooms that
are already running, and it is why the names are computed from the addresses
whether or not the feature is on.

WHAT ANSWERS MEANS. A reply, not a 200. `cdn.npmmirror.com` answers 404 for the
directory above the browsers and 200 for a browser; a mirror behind a private
index answers 403 to a HEAD it does not serve. All of those are the mirror
being there. Only no answer at all — no name, refused, timed out — is not.

Standard library only, and Python 3.9: shipped beside the executor's runtime
(`remote-execution/package_mirrors.py`) and exec'd standalone there, where it
cannot import `app.*`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

# One probe's budget, which is also what a launcher spends on the same probe
# (`shell` renders these). A mirror on a direct path answers in tens of
# milliseconds; these are the ceiling for one that never answers at all.
PROBE_CONNECT_S = 1.0
PROBE_TIMEOUT_S = 2.0
# How long an answer is kept, under the room's store. The executor prepares on
# every tool call it serves, so probing each time would be a probe per call and
# a room whose network is gone would pay the ceiling every call; this is also
# how long a mirror that goes down keeps taking a room's installs with it.
ANSWER_KEPT_S = 900.0

# What a probe of a mirror asks for: a URL on it that any mirror can answer.
# Curl exits 0 for a reply OF ANY KIND — including the 404 a browser mirror
# gives over the directory above its browsers, which is the mirror being there
# — and 22 when it is asked to refuse error statuses (`-f`), which nothing here
# passes but a machine's own `curl` wrapper might. Every other exit is the
# mirror not answering at all.
_ANSWERED_EXITS = (0, 22)
# The `curl` a launcher runs. Present on every machine a room launches on —
# Git Bash carries one on Windows — and a machine without it exports nothing.
#
# `--head`: the answer is whether the host replies, and a mirror should not
# have to serve a body to say so. Spelled out as one recognizable word because
# a probe is not a download — the launcher's own downloads are GETs, and a
# fixture counting them can tell the two apart by exactly this.
_CURL = "curl -s --head -o /dev/null"


@dataclass(frozen=True)
class Mirror:
    """One source a room's downloads can be moved to a domestic mirror.

    `key` names both the field of a room's configuration that carries the
    address and the `{...}` placeholder the value of an exported variable reads
    it back with: the pypi entry names `{pypi}` for the mirror and
    `{pypi_official}` for the index behind it.
    """

    key: str
    exports: tuple[tuple[str, str], ...]


# The mirrors, and what each one exports when it answers. Anything exported
# here reaches the agent's own shell, so a name added here is a behaviour an
# agent's tools have.
MIRRORS = (
    # uv and pip both, because uv reads none of pip's variables and pip reads
    # none of uv's: two indexes configured twice, over the same mirror.
    #
    # The official index is the second entry rather than the only one, so a
    # package is looked for there once a mirror does not have it. uv's default
    # index strategy takes the first index that has the *name*, which a mirror
    # that has synced the project but not the release fails on, so uv is given
    # `unsafe-first-match` — it falls through to the second index when the
    # first cannot resolve the package. There is no confusion to be had from
    # it: both of these indexes are the same public package set, and no second
    # one is anything but the official source.
    Mirror(
        key="pypi",
        exports=(
            ("UV_INDEX", "{pypi}"),
            ("UV_DEFAULT_INDEX", "{pypi_official}"),
            ("UV_INDEX_STRATEGY", "unsafe-first-match"),
            ("PIP_INDEX_URL", "{pypi}"),
            ("PIP_EXTRA_INDEX_URL", "{pypi_official}"),
        ),
    ),
    # Single-address tools from here down: nothing to fall back to inside the
    # tool, which is what the probe above is for.
    Mirror(key="npm", exports=(("npm_config_registry", "{npm}"),)),
    Mirror(key="playwright", exports=(("PLAYWRIGHT_DOWNLOAD_HOST", "{playwright}"),)),
    Mirror(key="uv_python", exports=(("UV_PYTHON_INSTALL_MIRROR", "{uv_python}"),)),
)


def configuration(settings) -> dict:
    """The mirror addresses one room is given, out of the platform's settings,
    and whether they are to be used at all.

    The addresses are named whether or not the feature is on, because
    `governed` is asked of this and a deployment that turns the feature off
    still has to take the variables back out of the rooms that are running.
    """
    return {
        "enabled": bool(settings.package_mirror_enabled),
        "pypi": settings.package_mirror_pypi_index,
        "pypi_official": settings.package_mirror_pypi_fallback,
        "npm": settings.package_mirror_npm_registry,
        "playwright": settings.package_mirror_playwright_host,
        "uv_python": settings.package_mirror_uv_python,
    }


def governed(config) -> dict:
    """Every variable this policy governs for one room, and what it puts there.

    Every mirror the deployment has given an address to, whether or not that
    mirror answers and whether or not the feature is on. An environment
    prepared without one of them is a room that has to stop using it, and this
    is the only thing that says so: a variable that is merely left out of an
    environment is one the room keeps (`runtime`, `configure`).

    The value travels with the name because a name does not say who filled it
    in: a project or a machine that named an index of its own holds another
    value under the same name, and that one is not this policy's to take away.
    Both come from `_exports`, so an export the deployment has no address for —
    the default `uv_python` among them — is not governed at all: it is a name
    this policy could never have set.
    """
    return {
        name: value
        for mirror in MIRRORS
        if config.get(mirror.key)
        for name, value in _exports(mirror, config)
    }


def environment(config, store=None, *, now=None) -> dict:
    """The variables for the mirrors in `config` that answer, and nothing for
    the ones that do not.

    `store` is the room's store: the answer is kept there, because this runs on
    the machine for every tool call (`bootstrap.prepared`). A store that cannot
    be written costs the next call a probe, not the room its installs.
    """
    if not config.get("enabled", True):
        return {}
    live = _live(config, store, now=now)
    exports = {}
    for mirror in MIRRORS:
        if not live.get(mirror.key):
            continue
        for name, value in _exports(mirror, config):
            exports[name] = value
    return exports


def shell(config) -> str:
    """`environment(config)`, as the statements a launcher script runs.

    The probe happens there rather than here on purpose: whether a mirror
    answers is a property of the machine the room runs on, and the backend that
    builds this script is not on the network of every machine it launches on.
    """
    if not config.get("enabled", True):
        return ""
    live = [
        (_probe_url(config[mirror.key]), _exports(mirror, config))
        for mirror in MIRRORS
        if config.get(mirror.key)
    ]
    live = [(url, exports) for url, exports in live if exports]
    if not live:
        return ""
    connect, total = int(PROBE_CONNECT_S), int(PROBE_TIMEOUT_S)
    answered = " || ".join(f'[ "$_probe" = {code} ]' for code in _ANSWERED_EXITS)
    lines = [
        "# Where this room's package downloads come from. A mirror is used only",
        "# once this says it answers; one that does not stays unset, and the tool",
        "# is on its official source. Nothing here fails the launch: a probe that",
        "# cannot run and a mirror that is not there both leave the environment",
        "# exactly as it was without this block.",
        "if command -v curl >/dev/null 2>&1; then",
    ]
    for url, exports in live:
        lines.append("  _probe=0")
        lines.append(
            f"  {_CURL} --connect-timeout {connect} --max-time {total} "
            f"{_quoted(url)} || _probe=$?"
        )
        lines.append(f"  if {answered}; then")
        # Each variable on its own, so a room that sets one of them itself
        # keeps it — the same rule `bootstrap.prepared` follows with the
        # room's configured variables.
        for name, value in exports:
            lines.append(
                f'    [ -n "${{{name}:-}}" ] || export {name}={_quoted(value)}'
            )
        lines.append("  fi")
    lines.append("  unset _probe")
    lines.append("fi")
    return "\n".join(lines)


def _exports(mirror, config):
    """The variables one mirror exports, as (name, value) pairs, without any
    whose value is an address nobody set."""
    exports = []
    for name, template in mirror.exports:
        value = template.format(**config)
        if value:
            exports.append((name, value))
    return exports


def _probe_url(address) -> str:
    """What decides whether a mirror is there: the mirror itself.

    Every one of these serves something at its own root — an index listing, a
    registry, or the 404 a browser mirror answers over the directory holding
    its browsers.
    """
    return address.rstrip("/") + "/"


def _live(config, store, *, now=None):
    """Which of `config`'s mirrors answer, out of the cache or out of probes."""
    wanted = {
        mirror.key: _probe_url(config[mirror.key])
        for mirror in MIRRORS
        if config.get(mirror.key)
    }
    if not wanted:
        return {}
    stamp = time.time() if now is None else now
    cached = _cached(store, config)
    if cached is not None and 0 <= stamp - cached["at"] < ANSWER_KEPT_S:
        return cached["reachable"]
    # Together: a probe that gets no answer waits out its whole budget, and
    # three of those in a row is a room that starts a tool call nine seconds
    # late. In parallel it is one budget, whatever the answer.
    with ThreadPoolExecutor(max_workers=len(wanted)) as probing:
        # Answering with the key rather than zipping a second sequence back up:
        # this module is shipped to rooms that run Python 3.9, which has no
        # `zip(..., strict=True)` for a check to ask for.
        reachable = dict(probing.map(lambda key: (key, _answers(wanted[key])), wanted))
    _keep(store, config, stamp, reachable)
    return reachable


def _answers(url) -> bool:
    """Whether the host behind `url` answers at all. Any status counts: a
    mirror refusing a HEAD is a mirror that is there."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, method="HEAD"), timeout=PROBE_TIMEOUT_S
        ):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        # DNS, a refused connection, a timeout, a TLS that will not come up:
        # every one of them is this mirror being of no use to this room, and
        # the answer is the same for all of them.
        return False


def _cached(store, config):
    """The last answer, or None when there is none or it was about other
    addresses — a mirror changed by a deployment is a mirror to probe again."""
    path = _cache_path(store)
    if path is None:
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or cached.get("config") != config:
        return None
    if not isinstance(cached.get("at"), (int, float)):
        return None
    if not isinstance(cached.get("reachable"), dict):
        return None
    return cached


def _keep(store, config, at, reachable) -> None:
    path = _cache_path(store)
    if path is None:
        return
    # Two prepares of one room can run at once, so the name is this process's
    # and the file is put in place by a rename: a reader sees the last answer
    # or this one, never the half of one.
    temporary = path.with_name(f"{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({"config": config, "at": at, "reachable": reachable}),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        # Only the probe is lost: the room has its answer for this call, and
        # the next one probes again.
        try:
            temporary.unlink()
        except OSError:
            pass


def _cache_path(store):
    return Path(store) / "package-mirrors.json" if store else None


def _quoted(value) -> str:
    """A string as one POSIX word, whatever it holds."""
    return "'" + value.replace("'", "'\\''") + "'"
