"""两版文档差在哪 —— 比的是正文，不是字节 (#1085 结论五)。

`.docx` 是个 zip，按字节比只答得出「不一样」。所以结论五承诺文档用户免费获得的
「版本、历史与差异」里，差异那一半一直是空的：产物页上摆着第 3 版和第 4 版，屏幕上
写的是「此格式不提供逐行差异」。

这里断言的是那句话被一份真的差异替掉了，以及它没有把话说大：读不出来的文件照旧退回
按字节比，而正文一个字没改、只改了版式的时候，说的是「正文相同」而不是「两版相同」。
"""

from __future__ import annotations

import zipfile

import pytest

pytest.importorskip(
    "lxml", reason="读 .docx 正文用的是 documents 技能里那个脚本，它要 lxml"
)

from app.domain.documents.text import delivered_comparison, document_text  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd'
    '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
    '/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def document(*paragraphs: str) -> bytes:
    """一份最小的 Word 包，正文是给进来的那几段。"""
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    return _zip(
        {
            "[Content_Types].xml": CONTENT_TYPES,
            "_rels/.rels": RELS,
            "word/document.xml": (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:document {W}><w:body>{body}</w:body></w:document>"
            ),
        }
    )


def _zip(parts: dict[str, str]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return buffer.getvalue()


def test_the_text_comes_out_a_paragraph_a_line():
    raw = document("第三季度预算说明", "本季度预算按旧的说法编制。")

    assert document_text(raw, "报告.docx") == (
        "第三季度预算说明\n本季度预算按旧的说法编制。\n"
    )


def test_a_file_that_is_not_a_document_reads_as_nothing():
    """读不出来就是读不出来，不是一条错误：调用方要退回按字节比。"""
    assert document_text(b"not an office package at all", "报告.docx") is None
    assert document_text(b"", "报告.docx") is None


def test_two_versions_are_compared_by_their_words():
    old = document("第三季度预算说明", "合计 120 万元。")
    new = document("第三季度预算说明", "合计 135 万元。")

    result = delivered_comparison(old, new, "报告.docx", "报告.docx")

    assert result["identical"] is False
    # 屏幕上要说清比的是什么：版式和修订记录不在这份差异里。
    assert result["note"] == "document"
    assert "-合计 120 万元。" in result["diff"]
    assert "+合计 135 万元。" in result["diff"]


def test_the_same_words_in_different_bytes_are_the_same_words():
    """两份字节不同、正文一字不差：这两版没有差异。

    一句话在文件里被拆在几个 run 里是常态（拼写检查、格式切换都会拆），拆法变了字节
    就变了，而读者读到的是同一句话。字节相等这个判据对文档因此永远偏严，而人在产物页
    上问的是「这一版比上一版改了什么」。
    """
    whole = document("第三季度预算说明", "合计 120 万元。")
    split = _zip(
        {
            "[Content_Types].xml": CONTENT_TYPES,
            "_rels/.rels": RELS,
            "word/document.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:document {W}><w:body>"
                "<w:p><w:r><w:t>第三季度预算说明</w:t></w:r></w:p>"
                '<w:p><w:r><w:t xml:space="preserve">合计 </w:t></w:r>'
                "<w:r><w:t>120 万元。</w:t></w:r></w:p>"
                "</w:body></w:document>"
            ),
        }
    )

    assert whole != split
    result = delivered_comparison(whole, split, "报告.docx", "报告.docx")

    assert result["identical"] is True
    assert result["note"] == "document"
    assert result["diff"] == ""


def test_something_unreadable_falls_back_to_comparing_bytes():
    """读不出正文的那一侧照旧走字节比较，屏幕上那句话也回到原来那一句。"""
    result = delivered_comparison(b"\x00\x01", b"\x00\x02", "报告.docx", "报告.docx")

    assert result["identical"] is False
    assert result["note"] == "binary"
    assert result["diff"] is None
