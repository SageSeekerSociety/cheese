// 一份 .docx 的正文，一段一行：两版 Word 之间「改了几处、改了什么」就拿它比。
//
// 只取文字，不取排版：版本对比要回答的是「这一版说的话和上一版有什么不同」，字号、
// 颜色改了在这里看不出来，这是有意的取舍（改格式在印刷版里自己看得见）。表格里每一格
// 的每一段也各算一行，所以改了表里一格的字同样数得出来。
//
// 修订里删掉的字（`w:delText`）不算正文：那是「建议删掉」，接受之前文档里仍然只剩
// 插入后的样子。插入的字（`w:ins` 里的 `w:t`）照常算。

import { readZipEntry } from './zipEntry'

const PARAGRAPH = /<w:p[\s>][\s\S]*?<\/w:p>/g
const RUN_PIECE = /<w:t(?:\s[^>]*)?>([^<]*)<\/w:t>|<w:tab\/>|<w:(?:br|cr)(?:\s[^>]*)?\/>/g

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }

function unescapeXml(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, code: string) => {
    if (code[0] !== '#') return ENTITIES[code.toLowerCase()] ?? ''
    const n = code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10)
    return Number.isFinite(n) ? String.fromCodePoint(n) : ''
  })
}

/** `document.xml` 的正文，一段一行（空段不算）。 */
export function documentXmlParagraphs(xml: string): string[] {
  const out: string[] = []
  for (const paragraph of xml.match(PARAGRAPH) ?? []) {
    let text = ''
    for (const piece of paragraph.matchAll(RUN_PIECE)) {
      if (piece[1] !== undefined) text += unescapeXml(piece[1])
      else text += piece[0].startsWith('<w:tab') ? '\t' : ' '
    }
    if (text.trim()) out.push(text)
  }
  return out
}

/** 这一份 .docx 的正文；读不开（不是 zip、没有正文那一条）时给 null。 */
export async function docxParagraphs(bytes: ArrayBuffer): Promise<string[] | null> {
  const xml = await readZipEntry(bytes, 'word/document.xml')
  if (!xml) return null
  return documentXmlParagraphs(new TextDecoder().decode(xml))
}
