// 一份 .pptx 每一页的演讲者备注。
//
// 备注不在幻灯片上，它在另一个部件里（`ppt/notesSlides/notesSlideN.xml`），一份文件里
// 有几页就最多有几个，而且**编号和页序不是一回事**：notesSlide3.xml 说的是哪一页，只有
// 幻灯片自己那条关系（`ppt/slides/_rels/slideN.xml.rels`）说得清。所以这里按
// `ppt/presentation.xml` 的那串 sldId 逐页走，顺着关系找它自己的备注，而不是从 1 数到 N
// 去猜。
//
// 备注页里除了演讲者写的那几段，还有占位符：`<p:sp>` 里 `<p:ph t="sldNum">` 那个是页码
// 框，`ftr`/`dt` 是页脚和日期。它们和正文是同一套 `<a:t>`，只按文字取会把页码当成备注的
// 第一段。所以取之前先看形状的 `ph`。
//
// 只有文字。备注页上的位置、字号在这里没有意义：它是拿来读的，而且 Powerpoint 里那些框
// 到了这里就是一串段落，没有「原样」可还原。

import { readZipEntry } from './zipEntry'

const PRESENTATION = 'ppt/presentation.xml'
const RELS = 'ppt/_rels/presentation.xml.rels'

/** 幻灯片里三种「不是演讲者写的」占位符：页码、页脚、日期。 */
const CHROME_PLACEHOLDERS = new Set(['sldNum', 'ftr', 'dt'])

/** XML 里那几个实体。备注是给模型读的，多留一个 `&amp;` 只会让一句话从中间断掉。 */
const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }

function unescapeXml(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, code: string) => {
    if (code[0] !== '#') return ENTITIES[code.toLowerCase()] ?? ''
    const n = code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10)
    return Number.isFinite(n) ? String.fromCodePoint(n) : ''
  })
}

/** 关系部件里 id → 整路径。
 *
 *  `base` 是这份 rels 归属的那个部件的路径（`ppt/presentation.xml`），因为 Target
 *  写的是**相对那个部件**的：`slides/slide1.xml`、`../notesSlides/notesSlide1.xml`
 *  两种写法在同一份文件里都出得来。 */
export function relationshipTargets(xml: string, base: string): Map<string, string> {
  const directory = base.split('/').slice(0, -1)
  const out = new Map<string, string>()
  for (const tag of xml.matchAll(/<Relationship\b[^>]*>/g)) {
    const id = /\bId="([^"]*)"/.exec(tag[0])?.[1]
    const target = /\bTarget="([^"]*)"/.exec(tag[0])?.[1]
    if (!id || !target) continue
    const parts = target.startsWith('/') ? [] : [...directory]
    for (const piece of target.split('/')) {
      if (!piece || piece === '.') continue
      if (piece === '..') parts.pop()
      else parts.push(piece)
    }
    out.set(id, parts.join('/'))
  }
  return out
}

/** 幻灯片自己的关系部件在哪儿：`ppt/slides/slide1.xml` → `ppt/slides/_rels/slide1.xml.rels`。 */
export function relsPathOf(part: string): string {
  const pieces = part.split('/')
  const name = pieces.pop() ?? ''
  return [...pieces, '_rels', `${name}.rels`].join('/')
}

/** 一个形状的正文：它自己那几段 `<a:p>`，一段一行。 */
const PARAGRAPH = /<a:p[\s>][\s\S]*?<\/a:p>/g
const RUN_TEXT = /<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g
/** 占位符类型。是 `type` 不是 `t`——PowerPoint 写的 `<p:ph type="sldNum" .../>` 里
 *  没有单字母的属性，按 `t` 找的话一个也匹配不上，页码就当成备注读进去了。 */
const PLACEHOLDER = /<p:ph\b[^>]*\btype="([^"]*)"/g

/** 备注页 XML 里演讲者写的那几段文字。页码、页脚、日期的框不算。 */
export function notesSlideText(xml: string): string {
  const lines: string[] = []
  for (const shape of xml.match(/<p:sp[\s>][\s\S]*?<\/p:sp>/g) ?? []) {
    // `<p:ph/>` 没有 type 的默认是正文占位符，不算这三种。
    if ([...shape.matchAll(PLACEHOLDER)].some((m) => CHROME_PLACEHOLDERS.has(m[1]))) continue
    for (const paragraph of shape.match(PARAGRAPH) ?? []) {
      const text = [...paragraph.matchAll(RUN_TEXT)].map((run) => unescapeXml(run[1])).join('')
      if (text.trim()) lines.push(text.trim())
    }
  }
  return lines.join('\n')
}

/** `<p:presentation>` 里那一串幻灯片关系 id，按放映顺序。 */
export function slideRelIds(xml: string): string[] {
  return [...xml.matchAll(/<p:sldId\b[^>]*\br:id="([^"]*)"/g)].map((m) => m[1])
}

/** 每一页的备注，下标就是页码（0 是第一页）。读不开给 null；某一页没有备注时那一项是空串。 */
export async function pptxNotes(bytes: ArrayBuffer): Promise<string[] | null> {
  const [deckRaw, relsRaw] = await Promise.all([
    readZipEntry(bytes, PRESENTATION),
    readZipEntry(bytes, RELS),
  ])
  if (!deckRaw || !relsRaw) return null
  const decoder = new TextDecoder()
  const slides = relationshipTargets(decoder.decode(relsRaw), PRESENTATION)
  const out: string[] = []
  for (const id of slideRelIds(decoder.decode(deckRaw))) {
    const slide = slides.get(id)
    if (!slide) {
      out.push('')
      continue
    }
    // 备注只从这一页**自己**那条关系找：同一张备注被两页共用时，关系里也只写它一次，
    // 那两页各自都读得到同一段。
    const own = await readZipEntry(bytes, relsPathOf(slide))
    const notesPart = own
      ? [...relationshipTargets(decoder.decode(own), slide).values()].find((part) => part.includes('/notesSlides/'))
      : undefined
    if (!notesPart) {
      out.push('')
      continue
    }
    const raw = await readZipEntry(bytes, notesPart)
    out.push(raw ? notesSlideText(decoder.decode(raw)) : '')
  }
  return out
}
