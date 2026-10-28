"""Reading a machine's skills off the machine: one stdlib program, one JSON line.

The platform never copies a skill, so it has to read the machine each time it
wants to know what is there (``machine_skill/models.py``). The read is one
one-shot command (``device_hub.exec``), the way ``machine/lifecycle.py`` asks a
host to do things: a program on stdin, JSON on the last line of stdout.

The program is stdlib-only on purpose — it runs on whatever ``python3`` the
machine has (the same assumption ``lifecycle.program`` makes), with no platform
code, no third-party import, nothing to install. Eleven lines of ``SKILL.md``
parsing is not worth a YAML dependency on the far side.

The roots are the directories skills already live in — every agent home a
machine might have, times the shapes skills sit under one — so a machine's
existing skills show up without anyone moving a file: ``<home>/skills`` (where
people keep their own for Claude Code, Codex, Trae, agents),
``<home>/builtin_skills`` and ``<home>/builtin/global/skills`` (what Trae
ships), and ``<home>/plugins/cache/*/*/*/skills`` (what an installed plugin
brought). Depth is 3 so ``~/.codex/skills/.system/<name>/SKILL.md`` is found. A
directory holding a ``SKILL.md`` is one skill and is not descended into: a
skill's own ``scripts/`` and ``references/`` are its business, not more skills.

Those homes are read only on a machine the platform provisioned (``cloud``,
argv[2] empty). On a machine the person enrolled themselves (``self_hosted``,
argv[2] ``"1"``) they are left out entirely: the ``~/.claude`` skills someone
wrote for their own agents are theirs, not this platform's to index, and
listing them would put a person's private setup on a shared screen. What
remains on such a machine is only what the platform manages — the workspaces
the person listed, a session's project skills, and the skills landed from the
internet.

A skill can also be scoped to a workspace, and then it is nowhere else: no home
holds a copy. A person lists those workspaces (``~/.cheese/skill-workspaces``,
one absolute path per line) and each one's ``.trae/skills`` is scanned beside
the homes. The list lives on the machine because the skills do — the platform
keeps no copy of it either.

Two more roots sit beside those, both read last so a skill the person keeps
themselves wins a name they share: the skills a *session* wrote for the project
being asked about (``~/.cheese/home/<project>/*/.claude/skills``, the project
arriving as argv[1]), and where a skill fetched from the internet was landed
(``~/.cheese/web-skills``, written by ``land_program`` below).

The rules the spec (agentskills.io) makes mandatory are the only ones checked —
``name`` equals the directory name, a ``^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$`` shape,
≤64, and a non-empty ``description`` ≤1024. Uppercase is let in because Trae
ships its own skills as ``TRAE-xxx``; the spec only mandates lowercase, so
refusing it would drop a whole platform's directory of working skills. A
violating directory is reported in ``problems`` and left out of the index, never
silently renamed.
"""

from __future__ import annotations

import json
from typing import Any


class ScanFailed(RuntimeError):
    """The machine could not answer the scan, or its answer was unreadable.

    Deliberately this module's own type, not ``device_hub.DeviceCallError``:
    ``machine_skill`` is read by the agent domain (``agent/carried_bodies.py``),
    and importing back into it would close a cycle between two sibling domains
    (``.importlinter`` C3). The route that runs the scan maps this to the
    machine-failure it is (``api/routes/machine_skills.py``).
    """


#: Marks a row whose body came from the internet (``land_program``). The platform
#: cannot know a machine's absolute home, so a landed row is recognised by this
#: fragment of its ``source_root``: the route refuses a fetched name already
#: taken by a skill that did *not* come this way, rather than silently
#: re-pointing an adopted row at a new body.
WEB_SKILLS_MARK = "/.cheese/web-skills/"


_PROGRAM = r'''
import glob, json, os, re, sys, hashlib

#: Every agent home a machine might have, in the order that also decides a name
#: two roots share (Claude's own wins over the Trae distributions').
AGENT_HOMES = [
    "~/.agents", "~/.claude", "~/.codex",
    "~/.trae", "~/.trae-cn", "~/.trae-enterprise", "~/.trae-student",
]
#: The shapes skills sit under a home: where people keep their own, what Trae
#: ships (``builtin_skills``, ``builtin/global/skills`` and one set per mode
#: under ``builtin/trae``), and what an installed plugin brought. Only installed
#: plugins are globbed — the marketplaces under ``.tmp`` list every skill on
#: offer, not the ones actually here.
SKILL_DIRS = [
    "skills", "builtin_skills", "builtin/global/skills", "builtin/trae/*/skills",
    "plugins/cache/*/*/*/skills",
]
#: Where a person lists the workspaces they work in (one absolute path per line,
#: ``#`` starts a comment). Each one's ``.trae/skills`` is scanned beside the
#: homes: a workspace-scoped skill exists nowhere else, so nothing else finds it.
REGISTRY = "~/.cheese/skill-workspaces"
#: The skills a session wrote for this project: one home per resource
#: generation, ``<generation>/.claude/skills``. Which project is asking arrives
#: as argv[1], because the machine cannot guess it and a scan for one project
#: must not list another's sessions.
SESSION_HOMES = "~/.cheese/home"
#: Where a skill fetched from the internet is landed (``land_program`` writes
#: it), read like any other root so a fetched skill behaves as a found one.
WEB_SKILLS = "~/.cheese/web-skills"
PROJECT = sys.argv[1] if len(sys.argv) > 1 else ""
#: "1" when the machine is the person's own (``self_hosted``): then the agent
#: homes above are not read at all. The ``~/.claude`` skills someone wrote for
#: their own agents are theirs, not this platform's to index.
SKIP_HOMES = len(sys.argv) > 2 and sys.argv[2] == "1"
MAX_DEPTH = 3
NAME_RE = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")


def listed():
    """The workspaces a person listed, expanded and de-duplicated."""
    try:
        with open(os.path.expanduser(REGISTRY), "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    seen, out = set(), []
    for line in lines:
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        path = os.path.expanduser(value)
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def roots():
    """Every home×shape directory that exists, plus each listed workspace's."""
    seen, out = set(), []

    def add(path):
        real = os.path.realpath(path)
        if os.path.isdir(path) and real not in seen:
            seen.add(real)
            out.append(path)

    if not SKIP_HOMES:
        for home in AGENT_HOMES:
            for shape in SKILL_DIRS:
                for path in sorted(glob.glob(os.path.expanduser(home + "/" + shape))):
                    add(path)
    for workspace in listed():
        add(os.path.join(workspace, ".trae", "skills"))
    if PROJECT:
        for path in sorted(
            glob.glob(
                os.path.join(
                    os.path.expanduser(SESSION_HOMES), PROJECT, "*", ".claude", "skills"
                )
            )
        ):
            add(path)
    add(os.path.expanduser(WEB_SKILLS))
    return out


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def reads_matter(text):
    """The top-level key/values of the YAML frontmatter, folded scalars included.

    Only ``name`` and ``description`` are read, and only the shapes skills
    actually write: inline, single-quoted, double-quoted, or a ``>``/``|``
    block. Anything else is read as its literal text rather than guessed at.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2)
        if value.strip() in (">", "|", ">-", "|-", ">+", "|+"):
            folded = value.strip()[0] == ">"
            block = []
            i += 1
            while i < len(lines) and (
                lines[i].startswith((" ", "\t")) or not lines[i].strip()
            ):
                block.append(lines[i].strip())
                i += 1
            if folded:
                out[key] = " ".join(part for part in block if part).strip()
            else:
                out[key] = "\n".join(block).strip()
            continue
        out[key] = unquote(value)
        i += 1
    return out


def record(path, root, dirname, found, problems):
    if dirname in found:
        return
    skill_md = os.path.join(path, "SKILL.md")
    try:
        with open(skill_md, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        problems.append("%s: cannot read SKILL.md (%s)" % (path, exc))
        return
    matter = reads_matter(raw.decode("utf-8", "replace"))
    name = matter.get("name") or dirname
    description = (matter.get("description") or "").strip()
    if name != dirname:
        problems.append("%s: name %r does not match the directory" % (path, name))
        return
    if not NAME_RE.match(name) or len(name) > 64:
        problems.append("%s: name %r is not a valid skill name" % (path, name))
        return
    if not description or len(description) > 1024:
        problems.append("%s: description is missing or too long" % path)
        return
    found[name] = {
        "name": name,
        "description": description,
        "source_root": path,
        "digest": hashlib.sha256(raw).hexdigest(),
    }


def descend(path, found, problems, depth):
    if depth > MAX_DEPTH:
        return
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return
    for entry in entries:
        child = os.path.join(path, entry)
        if not os.path.isdir(child):
            continue
        if os.path.isfile(os.path.join(child, "SKILL.md")):
            record(child, path, entry, found, problems)
        else:
            descend(child, found, problems, depth + 1)


found, problems = {}, []
scanned = roots()
for root in scanned:
    descend(root, found, problems, 0)
print(json.dumps({
    "skills": list(found.values()),
    "problems": problems,
    "roots": scanned,
    "workspaces": listed(),
}))
'''

#: The other half of the registry: recording what a person listed. The paths
#: arrive as argv (the program itself is on stdin, the way every one-shot command
#: here is), so a path with a space needs no quoting.
_SET_ROOTS_PROGRAM = r"""
import os, sys

REGISTRY = "~/.cheese/skill-workspaces"

target = os.path.expanduser(REGISTRY)
try:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("# 本机上要一起扫技能的工作区根，每行一个绝对路径。\n")
        for path in sys.argv[1:]:
            handle.write(path + "\n")
except OSError as exc:
    sys.stderr.write("cannot write %s (%s)" % (target, exc))
    sys.exit(1)
"""


def program() -> str:
    """The whole one-shot program, for ``device_hub.exec(..., stdin=...)``."""
    return _PROGRAM


def set_program() -> str:
    """The one-shot program that records the workspaces, paths passed as argv."""
    return _SET_ROOTS_PROGRAM


#: Landing a fetched skill. The files are embedded in the program itself, because
#: a one-shot command's stdin *is* the program and argv is for scalars — the name
#: and the file map are carried as one JSON literal, so nothing needs quoting.
_LAND_PROGRAM = r"""
import json, os, sys

PAYLOAD = json.loads(__DATA__)
root = os.path.expanduser("~/.cheese/web-skills")
target = os.path.join(root, PAYLOAD["name"])


def unsafe(rel):
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    return not parts or rel.startswith("/") or ".." in parts


try:
    for rel, text in sorted(PAYLOAD["files"].items()):
        if unsafe(rel):
            sys.stderr.write("refusing an unsafe path: %s" % rel)
            sys.exit(1)
        full = os.path.join(target, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
except OSError as exc:
    sys.stderr.write("cannot write %s (%s)" % (target, exc))
    sys.exit(1)
print(json.dumps({"name": PAYLOAD["name"], "path": target}))
"""


def land_program(name: str, files: dict[str, str]) -> str:
    """The one-shot program that writes a fetched skill under ``~/.cheese/web-skills``.

    ``ponytail:`` the whole skill rides one ws frame, capped by
    ``project_skill.importer.MAX_TOTAL_BYTES`` (2 MB) at the caller — the same
    ceiling a repo read already answers to. Upgrade path: chunk the files.
    """
    payload = json.dumps({"name": name, "files": files}, ensure_ascii=False)
    return _LAND_PROGRAM.replace("__DATA__", json.dumps(payload, ensure_ascii=False))


def read(result: dict[str, Any]) -> dict[str, Any]:
    """What the program said, or the machine's own failure.

    The result is the machine's word across a process boundary, so it is read
    defensively: a skill row without the three strings the index needs is
    dropped rather than trusted into a ``KeyError``. A failed program is the
    machine's failure and travels as such (``ScanFailed``, which the route turns
    into the 502 that carries the machine's own words), never a 500 of this
    server's.
    """
    if result.get("exit") != 0 or result.get("truncated"):
        # i18n-exempt: 机器自己的 stderr，由 502 原样交给调用方
        raise ScanFailed(str(result.get("stderr") or "skill scan failed")[-1500:])
    try:
        said = json.loads(str(result["stdout"]).strip().splitlines()[-1])
    except (ValueError, IndexError, KeyError) as exc:
        # i18n-exempt: 机器回了一句读不懂的东西，把 json 的报错原样说出来。
        raise ScanFailed(f"skill scan said nothing readable: {exc}") from exc
    skills = said.get("skills") if isinstance(said, dict) else None
    if not isinstance(skills, list):
        # i18n-exempt: 同上 —— 机器回的 payload 不合约定，这句话是给排错的人看的。
        raise ScanFailed("skill scan returned no skills list")
    return {
        "skills": [row for row in (_reading(skill) for skill in skills) if row],
        "problems": [str(problem) for problem in said.get("problems") or []],
        "roots": [str(root) for root in said.get("roots") or []],
        "workspaces": [str(path) for path in said.get("workspaces") or []],
    }


def _reading(skill: Any) -> dict[str, str] | None:
    """One skill row, if it carries the three strings the index needs."""
    if not isinstance(skill, dict):
        return None
    name, root, digest = (
        skill.get("name"),
        skill.get("source_root"),
        skill.get("digest"),
    )
    if not all(isinstance(value, str) and value for value in (name, root, digest)):
        return None
    description = skill.get("description")
    # ``name``/``root``/``digest`` passed the isinstance trio above; str() says so
    # to the checker, which cannot carry that narrowing through all().
    return {
        "name": str(name),
        "source_root": str(root),
        "digest": str(digest),
        "description": description if isinstance(description, str) else "",
    }
