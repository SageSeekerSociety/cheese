"""一份文档读出来是什么字，给「这一版和上一版差在哪」用。

一份 `.docx` 是个 zip，所以按字节比两版只答得出「不一样」——产物页上那句「此格式不
提供逐行差异」就是这么来的。而 #1085 结论五说文档用户免费获得版本、历史与差异，差异
那一半因此一直是空的：交出去过七版报告，没有一版说得出它比上一版改了什么。

比的是正文，不是字节。段落是这份文件自己的段落，段序是读者读到它们的顺序——和
`office.py text` 印出来的那份、和修订面板上的段号是同一个答案（见 `revisions.py` 开头
那段：判断只有一处，两处实现必然漂）。所以这里同样不自己解 XML，加载那个脚本。

读不出来就是读不出来：返回 None，让调用方退回它本来就有的那句话。一份加了密的、坏掉
的、或者根本不是 Office 包的文件不该让一次版本比较变成一条错误——人要的是「这两版差在
哪」，答不出的时候说答不出。
"""

from __future__ import annotations

from app.domain.documents.revisions import package, script
from app.domain.textfile import compare_bytes, text_comparison

#: 能读出正文的两种包。Excel 的文字不在这些部件里（`office.py` 的 `text` 子命令会
#: 明说要用 openpyxl），所以 `.xlsx` 走不到这条路，退回按字节比。
_KINDS = ("word", "slide")


def document_text(raw: bytes, path: str) -> str | None:
    """这份文档的正文，一段一行；读不出来是 None。

    每段前面不带段号：这份文本要进 unified diff，而段号会让插入一段之后的每一段都
    变成一处改动。
    """
    office = script()
    try:
        opened, temporary = package(raw, path, office, kinds=_KINDS)
    except Exception:  # noqa: BLE001 — 读不出就是读不出，见模块开头
        return None
    try:
        kind = opened.kind()
        parts = opened.text_parts()
        if not parts:
            return None
        paragraph_tag = office._tags(kind)[0]
        lines: list[str] = []
        for name in parts:
            root = opened.elements(name)
            for paragraph in root.iter(paragraph_tag):
                lines.append(office.paragraph_text(paragraph, kind))
        # 末尾补一个换行：不补的话 unified diff 会在最后一段上挂一句「没有行尾换行」，
        # 而那不是这两版之间的差别。
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001 — 同上
        return None
    finally:
        temporary.unlink(missing_ok=True)


def delivered_comparison(old: bytes, new: bytes, left: str, right: str) -> dict:
    """这两版差在哪 (#1085 结论五)。

    Office 文档先按正文比，其余按字节比。按字节比文档只答得出「不一样」——`.docx` 是
    个 zip，所以交出去过七版报告，没有一版说得出它比上一版改了什么，而结论五说文档
    用户免费获得版本、历史与差异。

    正文比出来的 `identical` 说的就是正文，`note` 把这件事写在屏幕上：版式改了而一个
    字没改的时候，「正文没有变化」是真话，「两个版本的内容相同」不是。
    """
    old_text, new_text = document_text(old, left), document_text(new, right)
    if old_text is None or new_text is None:
        return compare_bytes(old, new, left, right)
    return text_comparison(
        old_text,
        new_text,
        left,
        right,
        identical=old_text == new_text,
        note="document",
    )
