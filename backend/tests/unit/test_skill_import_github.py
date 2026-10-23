"""A skill folder on GitHub is read as the folder holds it, from any link a
person would copy: the folder, the SKILL.md in it, or a repository whose root
is the skill. Nothing outside the folder comes along."""

import base64

import httpx
import pytest

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.domain.project_skill.importer import github_headers, read_github

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
    "skills/docx/SKILL.md": "---\nname: docx\ndescription: 写 Word\n---\n\n正文。\n",
}


def _file(text: str) -> dict:
    """一份文件的 contents 回包：base64，和 api.github.com 一样。"""
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def _github(seen: list[str]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host != "api.github.com":
            return httpx.Response(404)  # 一切都走 API 主机，文件也是
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": TREE})
        prefix = "/repos/anthropics/skills/contents/"
        if request.url.path.startswith(prefix):
            path = request.url.path.removeprefix(prefix)
            if path in RAW:
                return httpx.Response(200, json=_file(RAW[path]))
        return httpx.Response(404, json={})

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
    with pytest.raises(UnprocessableEntityError) as caught:
        await read_github(
            "https://github.com/anthropics/skills/tree/main/skills", _github([])
        )
    assert getattr(caught.value.message, "key", None) == "skillImportNoSkillMd"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/anthropics/skills",
        "https://github.com/anthropics/skills/tree/main",
    ],
)
async def test_a_repository_root_reads_every_bundled_skill(url):
    """插件形态：根下没有 SKILL.md，但 `skills/<名字>/SKILL.md` 各是一颗。"""
    from app.domain.project_skill.importer import read_github_many

    seen: list[str] = []
    reads = await read_github_many(url, _github(seen))

    assert [read.name for read in reads] == ["docx", "pdf"]
    assert reads[1].files == {"scripts/merge.py": "print(1)\n"}
    assert reads[1].skipped == ["fonts/a.ttf"]


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
    with pytest.raises(UnprocessableEntityError):
        await read_github(url, _github(seen))
    assert seen == []


def test_reads_carry_the_token_when_one_is_configured(monkeypatch):
    """未认证每小时只有 60 次，和「看市场」共用；配了 token 就是 5000。"""
    monkeypatch.setattr(settings, "github_token", "")
    assert "Authorization" not in github_headers()

    monkeypatch.setattr(settings, "github_token", "  ghp_test  ")
    assert github_headers()["Authorization"] == "Bearer ghp_test"
