"""任意 GitHub 仓库 → 技能：有 SKILL.md 就照读（不问模型），没有就让模型看着
README、目录树和入口文件写一份草稿，草稿带出处。"""

import base64
from types import SimpleNamespace

import httpx
import pytest
import yaml

from app.core.errors import UnprocessableEntityError
from app.domain.project_skill.repo_to_skill import repo_to_skill

SKILL = """---
name: pdf
description: 需要处理 PDF 时
---

# PDF 处理

先取文本。
"""

DRAFT = """---
name: 模型写错了的名字
description: 当你要处理 widget 时用这个，即使你没说出 widget 这个词
---

# Widget

## 用的时候

1. 装依赖。
"""

WITH_SKILL = [
    {"path": "SKILL.md", "type": "blob", "size": len(SKILL)},
    {"path": "scripts/merge.py", "type": "blob", "size": 9},
]
PLAIN = [
    {"path": "README.md", "type": "blob", "size": 12},
    {"path": "src/widget.py", "type": "blob", "size": 40},
    {"path": "pyproject.toml", "type": "blob", "size": 30},
]
NO_README = [{"path": "src/widget.py", "type": "blob", "size": 40}]
RAW = {
    "SKILL.md": SKILL,
    "scripts/merge.py": "print(1)\n",
    "README.md": "# Widget\n\n做个 widget。\n",
    "pyproject.toml": "[project]\nname = 'widget'\n",
}


class _Chat:
    """站位的模型：记下你让它读了什么，回一段固定的 SKILL.md。"""

    def __init__(self, text: str = DRAFT) -> None:
        self._text = text
        self.prompt = ""
        self.system = ""

    async def complete(self, *, system: str, prompt: str, timeout: float):
        self.system, self.prompt = system, prompt
        return SimpleNamespace(content=self._text)


def _never_called() -> _Chat:
    raise AssertionError("这个仓库有 SKILL.md，不该去问模型")


def _giving(chat: _Chat):
    """`repo_to_skill` 收的是个工厂（有 SKILL.md 时一次都不调用），包一层。"""

    async def make() -> _Chat:
        return chat

    return make


def _file(text: str) -> dict:
    """一份文件的 contents 回包：base64，和 api.github.com 一样。"""
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def _github(tree: list[dict], *, truncated: bool = False) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.github.com":
            return httpx.Response(404)  # 一切都走 API 主机，文件也是
        if request.url.path.endswith("/git/trees/HEAD"):
            return httpx.Response(200, json={"tree": tree, "truncated": truncated})
        prefix = "/repos/acme/widget/contents/"
        if request.url.path.startswith(prefix):
            path = request.url.path.removeprefix(prefix)
            if path in RAW:
                return httpx.Response(200, json=_file(RAW[path]))
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


URL = "https://github.com/acme/widget"


@pytest.mark.anyio
async def test_a_repository_holding_a_skill_md_is_read_without_a_model():
    read = await repo_to_skill(URL, _never_called, _github(WITH_SKILL))

    assert read.name == "pdf" and read.title == "PDF 处理"
    assert read.files == {"scripts/merge.py": "print(1)\n"}
    assert read.notes == []


@pytest.mark.anyio
async def test_a_repository_without_a_skill_md_is_drafted_from_its_readme():
    chat = _Chat()
    read = await repo_to_skill(URL, _giving(chat), _github(PLAIN))

    # 名字取仓库名，不取模型自己写的那个：目录名要等于 name。
    assert read.name == "widget"
    assert read.description.startswith("当你要处理 widget 时用")
    assert "# Widget" in read.body
    # 出处写进 frontmatter，人才看得出这是草稿、从哪来、什么时候生成的。
    front = yaml.safe_load(read.body.split("---", 2)[1])
    assert front["source"] == URL
    assert front["generated_at"].endswith("Z")
    assert front["name"] == "widget"
    # 只喂了三样，都喂到了。
    assert "做个 widget。" in chat.prompt and "pyproject.toml" in chat.prompt
    assert "src/widget.py" in chat.prompt


@pytest.mark.anyio
async def test_a_truncated_tree_is_told_to_the_person():
    read = await repo_to_skill(URL, _giving(_Chat()), _github(PLAIN, truncated=True))

    assert [getattr(note, "key", None) for note in read.notes] == [
        "skillImportTruncated"
    ]


@pytest.mark.anyio
async def test_a_repository_with_neither_skill_md_nor_readme_is_refused():
    with pytest.raises(UnprocessableEntityError) as caught:
        await repo_to_skill(URL, _giving(_Chat()), _github(NO_README))

    assert getattr(caught.value.message, "key", None) == "skillImportNoReadme"


@pytest.mark.anyio
async def test_a_branch_that_is_not_there_is_refused():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with pytest.raises(UnprocessableEntityError) as caught:
        await repo_to_skill(URL, _giving(_Chat()), client)

    assert getattr(caught.value.message, "key", None) == "skillImportGithubMissing"
