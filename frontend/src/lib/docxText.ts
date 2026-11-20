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

const STYLE = /<w:pStyle\s+w:val="([^"]*)"/
const STYLE_DEF = /<w:style\b[^>]*\bw:styleId="([^"]*)"[^>]*>[\s\S]*?<w:name\s+w:val="([^"]*)"/g
const HEADING_NAME = /^heading\s*(\d)$/i

/** 样式编号 → 样式名。中文版 Word 存的编号常是「1」「2」这种，认标题得看名字（`heading 1`），
 *  名字在 `styles.xml` 里；没有这一份时就拿编号本身当名字。 */
export function styleNames(stylesXml: string | null): Map<string, string> {
  const names = new Map<string, string>()
  for (const m of stylesXml?.matchAll(STYLE_DEF) ?? []) names.set(m[1], m[2])
  return names
}

/** 段落样式说它是标题、列表的，按 Markdown 的写法在行首标出来（`# `、`- `）：对比视图
 *  据此把它排成标题、列表（`PreviewVersionDiff` 认的是同一套符号），两版之间把一段从
 *  正文改成标题也算改了一处。 */
function markerOf(paragraph: string, names: Map<string, string>): string {
  const id = STYLE.exec(paragraph)?.[1] ?? ''
  const name = names.get(id) ?? id
  if (/^title$/i.test(name)) return '# '
  const level = HEADING_NAME.exec(name)?.[1] ?? HEADING_NAME.exec(id)?.[1]
  if (level) return `${'#'.repeat(Math.min(Number(level) + 1, 6))} `
  if (/<w:numPr>/.test(paragraph) || /^list/i.test(name)) return '- '
  return ''
}

/** `document.xml` 的正文，一段一行（空段不算）。标题、列表段在行首带 `# `、`- `。 */
export function documentXmlParagraphs(xml: string, names: Map<string, string> = new Map()): string[] {
  const out: string[] = []
  for (const paragraph of xml.match(PARAGRAPH) ?? []) {
    let text = ''
    for (const piece of paragraph.matchAll(RUN_PIECE)) {
      if (piece[1] !== undefined) text += unescapeXml(piece[1])
      else text += piece[0].startsWith('<w:tab') ? '\t' : ' '
    }
    if (text.trim()) out.push(markerOf(paragraph, names) + text)
  }
  return out
}

/** 这一份 .docx 的正文；读不开（不是 zip、没有正文那一条）时给 null。 */
export async function docxParagraphs(bytes: ArrayBuffer): Promise<string[] | null> {
  const [xml, styles] = await Promise.all([
    readZipEntry(bytes, 'word/document.xml'),
    readZipEntry(bytes, 'word/styles.xml'),
  ])
  if (!xml) return null
  const decoder = new TextDecoder()
  return documentXmlParagraphs(decoder.decode(xml), styleNames(styles && decoder.decode(styles)))
}
