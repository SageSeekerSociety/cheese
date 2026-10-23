"""`figma.py` 从网址里认出 file key 和画板 id——认错了就去读别人的稿子，或者 404。

Figma 的网址几种形状都还在用（`/file/` 老的、`/design/` 新的），画板 id 在网址里写成
`node-id=12-34`，而 API 要 `12:34`。这一段是脚本里唯一会算错的地方，所以钉在这里。

源码直接 `exec` 进来，不经 import：import 会在这份技能目录里落一个 `__pycache__`，
而 `test_native_skill_files.py` 要求技能目录里一个多余文件都没有。
"""

from __future__ import annotations

from app.domain.skill_library import _NATIVE_SKILL_SRC

FIGMA = _NATIVE_SKILL_SRC / "showcase" / "scripts" / "figma.py"


def _target(value: str) -> tuple[str, str]:
    namespace: dict = {"__name__": "figma_script", "__file__": str(FIGMA)}
    exec(compile(FIGMA.read_text(encoding="utf-8"), str(FIGMA), "exec"), namespace)
    return namespace["target"](value)


def test_a_design_url_gives_up_its_key_and_its_board():
    url = "https://www.figma.com/design/AbC123/My-Design?node-id=12-34&t=x"
    assert _target(url) == ("AbC123", "12:34")


def test_a_legacy_url_and_a_bare_key_still_read():
    assert _target("https://www.figma.com/file/AbC123/Name")[0] == "AbC123"
    assert _target("AbC123") == ("AbC123", "")
