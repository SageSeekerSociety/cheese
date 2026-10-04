"""A skill folder on GitHub is read as the folder holds it, from any link a
person would copy: the folder, the SKILL.md in it, or a repository whose root
is the skill. Nothing outside the folder comes along."""

import httpx
import pytest

from app.core.errors import ValidationError
from app.domain.project_skill.importer import read_github

SKILL = """---
name: pdf
description: 需要处理 PDF 时
---

# PDF 处理

先取文本。
"""

TREE = [
    {"path": "README.md", "type": "blob", "size": 10},
    {"path": "skills/pdf", "type": "tree"},
    {"path": "skills/pdf/SKILL.md", "type": "blob", "size": len(SKILL)},
    {"path": "skills/pdf/scripts/merge.py", "type": "blob", "size": 9},
    {"path": "skills/pdf/fonts/a.ttf", "type": "blob", "size": 100},
    {"path": "skills/docx/SKILL.md", "type": "blob", "size": 5},
]
RAW = {
    "skills/pdf/SKILL.md": SKILL,
    "skills/pdf/scripts/merge.py": "print(1)\n",
}


def _github(seen: list[str]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.github.com":
            if "/repos/anthropics/skills/git/trees/main" in request.url.path:
                return httpx.Response(200, json={"tree": TREE})
            return httpx.Response(404, json={})
        prefix = "/anthropics/skills/main/"
        path = request.url.path.removeprefix(prefix)
        if request.url.host == "raw.githubusercontent.com" and path in RAW:
            return httpx.Response(200, text=RAW[path])
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/anthropics/skills/tree/main/skills/pdf",
        "https://github.com/anthropics/skills/blob/main/skills/pdf/SKILL.md",
    ],
)
async def test_a_folder_link_reads_that_folder_only(url):
    seen: list[str] = []
    read = await read_github(url, _github(seen))

    assert read.name == "pdf" and read.title == "PDF 处理"
    assert read.files == {"scripts/merge.py": "print(1)\n"}
    assert read.skipped == ["fonts/a.ttf"]
    assert not any("docx" in url or "README" in url for url in seen)


@pytest.mark.anyio
async def test_a_folder_without_a_skill_md_is_refused():
    with pytest.raises(ValidationError) as caught:
        await read_github(
            "https://github.com/anthropics/skills/tree/main/skills", _github([])
        )
    assert getattr(caught.value.message, "key", None) == "skillImportNoSkillMd"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/anthropics/skills/tree/main/skills/pdf",
        "https://github.com.evil.example/anthropics/skills",
        "file:///etc/passwd",
    ],
)
async def test_only_github_is_read(url):
    seen: list[str] = []
    with pytest.raises(ValidationError):
        await read_github(url, _github(seen))
    assert seen == []
