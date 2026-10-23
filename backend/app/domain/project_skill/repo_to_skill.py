"""Turn any public GitHub repository into a skill.

A repository that already holds a ``SKILL.md`` is read as it is
(``importer.read_github``); one that does not is asked of the model, from three
bounded things only: the README (64 KB), the path-only directory tree, and the
entry files — ``pyproject.toml`` / ``package.json`` / ``Makefile`` /
``Cargo.toml`` (32 KB each). The draft carries its provenance in the
frontmatter (``source``, ``generated_at``), so a person reading the preview
knows what it was grown from.

Nothing is saved here: the shape that comes back is the same ``ReadSkill`` the
import preview already lays out, so 「预览 → 保存」 is untouched.

``ponytail:`` 生成是**读 README 猜的**，不是读过全部源码；大仓库的树会截断，
截断了就把这事实写进 ``notes``（``skillImportTruncated``）。升级路径：允许
指定子目录，把范围缩到一个包。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import SystemBusyError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.gateway_chat import GatewayChat
from app.domain.project_skill.importer import (
    _FRONTMATTER,
    GithubPlace,
    ReadSkill,
    _safe,
    _slug,
    fetch_file,
    github_headers,
    parse_github_url,
    parse_skill_md,
    read_github,
)
from app.domain.service_keys import KeySpec, service_key

#: What the README may weigh in the prompt, and each entry file.
README_BYTES = 64_000
ENTRY_BYTES = 32_000
#: How many paths of the tree go in before it is cut.
TREE_PATHS = 1200

README_NAMES = ("README.md", "readme.md", "README.rst", "README.txt")
ENTRY_NAMES = ("pyproject.toml", "package.json", "Makefile", "Cargo.toml")

_SYSTEM = (
    "你在为一个 agent 技能库写一份 SKILL.md 草稿。你只能看到这个仓库的 README、"
    "入口文件和目录结构，看不到全部源码——只根据这些写，不要编造没见过的文件、"
    "命令或参数。\n"
    "SKILL.md 的格式：开头一段 frontmatter，只有两个字段——\n"
    "name：技能的短名字，小写字母、数字和连字符；\n"
    "description：**唯一的触发机制**。写清楚「什么时候该用这个技能」，"
    "主动一点：把用户可能说的场景、关键词、同义说法都点到，"
    "即使他没说出这个技能的名字，也要能被触发。\n"
    "frontmatter 之后是正文（Markdown）：先用一句话说这个技能能做什么，"
    "再给「## 用的时候」一节列出可操作的步骤。正文写短，控制在 500 行以内。\n"
    "直接输出 SKILL.md 全文，不要用 ``` 代码块包起来。"
)


def draft_key_spec() -> KeySpec:
    """The gateway key a repo draft is made on. No budget of its own: each
    preview is paid for from the manager's personal credits."""
    return KeySpec(
        name="skill-draft-gateway-key",
        alias="skill-draft",
        model=settings.skill_draft_model,
        budget_usd=None,
        rpm=120,
    )


async def draft_chat(db: AsyncSession) -> GatewayChat:
    """The model that writes a SKILL.md draft, keyed like the PDF drafter."""
    key = await service_key(db, draft_key_spec())
    if key is None:
        raise SystemBusyError(say("skillDraftNotOpen"))
    return GatewayChat(
        key, settings.skill_draft_model, max_tokens=settings.skill_draft_max_tokens
    )


def _lookup(prefix: str, names: tuple[str, ...], blobs: dict[str, int]) -> str | None:
    """The first of ``names`` that exists, at the requested path or the repo root."""
    for name in names:
        for path in (prefix + name, name) if prefix else (name,):
            if path in blobs:
                return path
    return None


def _capped(data: bytes, limit: int) -> tuple[str, bool]:
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


def _tree(blobs: dict[str, int], prefix: str) -> tuple[list[str], bool]:
    paths = sorted(
        (p[len(prefix) :] for p in blobs if p.startswith(prefix)) if prefix else blobs
    )
    return paths[:TREE_PATHS], len(paths) > TREE_PATHS


def _prompt(
    place: GithubPlace,
    readme: str,
    tree: list[str],
    entries: list[tuple[str, str]],
) -> str:
    lines = [f"仓库：{place.owner}/{place.repo}", "", "## README", readme]
    lines += ["", f"## 目录（{len(tree)} 个文件）", "\n".join(tree)]
    for name, text in entries:
        lines += ["", f"## {name}", text]
    lines += ["", "请据此写出这份仓库的 SKILL.md。"]
    return "\n".join(lines)


def _after_frontmatter(text: str) -> str:
    """Everything past the leading frontmatter, heading kept."""
    match = _FRONTMATTER.match(text)
    return (text[match.end() :] if match else text).strip()


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", stripped, re.DOTALL)
    return match.group(1) if match else stripped


def _draft(raw: str, place: GithubPlace, url: str, notes: Sequence[str]) -> ReadSkill:
    """The model's SKILL.md, with provenance forced into its frontmatter."""
    cleaned = _strip_fences(raw).lstrip("\ufeff")
    parsed = parse_skill_md(cleaned, folder=place.repo)
    name = _slug(place.repo) or parsed.name
    if not name:
        raise UnprocessableEntityError(say("skillNameInvalid"))
    description = parsed.description.strip()[:1024]
    if not description:
        raise UnprocessableEntityError(say("skillImportFrontmatter"))
    front = {
        "name": name,
        "description": description,
        "source": url.strip(),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    draft = (
        "---\n"
        + yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + _after_frontmatter(cleaned)
        + "\n"
    )
    return ReadSkill(
        name=name,
        title=parsed.title or name,
        description=description,
        body=draft,
        notes=list(notes),
    )


async def repo_to_skill(
    url: str,
    chat: Callable[[], Awaitable[GatewayChat]],
    client: httpx.AsyncClient | None = None,
) -> ReadSkill:
    """A skill from a GitHub repository: read if it holds a ``SKILL.md``,
    otherwise written by the model ``chat`` builds.

    ``chat`` is a factory, not the chat itself: a repository that already holds a
    ``SKILL.md`` is read without a model at all, so a gateway key that is not
    open must not stop that reading (``import-preview`` took that URL before and
    answered it with no model in the path).
    """
    place = parse_github_url(url)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
    try:
        tree = await client.get(
            f"https://api.github.com/repos/{place.owner}/{place.repo}"
            f"/git/trees/{quote(place.ref, safe='')}",
            params={"recursive": "1"},
            headers=github_headers(),
        )
        if tree.status_code == 404:
            raise UnprocessableEntityError(say("skillImportGithubMissing"))
        if tree.status_code != 200:
            raise UnprocessableEntityError(say("skillImportGithubFailed"))
        payload = tree.json()
        truncated = bool(payload.get("truncated"))
        blobs = {
            item["path"]: int(item.get("size") or 0)
            for item in payload.get("tree", [])
            if item.get("type") == "blob" and _safe(item.get("path", ""))
        }
        prefix = f"{place.path}/" if place.path else ""

        # 直读：仓库/子目录里已经有 SKILL.md，就照读，不问模型。
        if f"{prefix}SKILL.md" in blobs:
            read = await read_github(url, client)
            if truncated:
                read.notes.append(say("skillImportTruncated"))
            return read

        readme_path = _lookup(prefix, README_NAMES, blobs)
        if readme_path is None:
            raise UnprocessableEntityError(say("skillImportNoReadme"))
        readme = await fetch_file(
            client, place.owner, place.repo, place.ref, readme_path
        )
        if readme is None:
            raise UnprocessableEntityError(say("skillImportGithubFailed"))
        readme_text, readme_cut = _capped(readme, README_BYTES)

        entries: list[tuple[str, str]] = []
        for name in ENTRY_NAMES:
            path = _lookup(prefix, (name,), blobs)
            data = (
                await fetch_file(client, place.owner, place.repo, place.ref, path)
                if path
                else None
            )
            if data is not None:
                entries.append((name, _capped(data, ENTRY_BYTES)[0]))

        tree_lines, tree_cut = _tree(blobs, prefix)
        notes = (
            [say("skillImportTruncated")] if truncated or readme_cut or tree_cut else []
        )
        answer = await (await chat()).complete(
            system=_SYSTEM,
            prompt=_prompt(place, readme_text, tree_lines, entries),
            timeout=settings.skill_draft_timeout_s,
        )
        return _draft(answer.content, place, url, notes)
    except httpx.HTTPError as exc:
        raise UnprocessableEntityError(say("skillImportGithubFailed")) from exc
    finally:
        if owns:
            await client.aclose()
