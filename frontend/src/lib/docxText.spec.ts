/**
 * Word 两版之间比的是这一份正文：一段一行，标题和列表在行首标出来，删掉的修订不算。
 * 字节是真的 zip（存储和 deflate 两种条目都有），走的是 `readZipEntry` 的完整路径。
 */
import { describe, expect, it } from 'vitest'

import { documentXmlParagraphs, docxParagraphs, styleNames } from './docxText'
import { readZipEntry } from './zipEntry'

async function deflate(raw: Uint8Array<ArrayBuffer>): Promise<Uint8Array> {
  const source = new ReadableStream<BufferSource>({
    start(controller) {
      controller.enqueue(raw)
      controller.close()
    },
  })
  const reader = source.pipeThrough(new CompressionStream('deflate-raw')).getReader()
  const parts: number[] = []
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(...value)
  }
  return new Uint8Array(parts)
}

/** 一个最小的 zip：每一条按给的方式存（0 原样，8 deflate），带中央目录。 */
async function zip(entries: { name: string; text: string; method: 0 | 8 }[]): Promise<ArrayBuffer> {
  const enc = new TextEncoder()
  const locals: Uint8Array[] = []
  const centrals: Uint8Array[] = []
  let offset = 0
  for (const entry of entries) {
    const name = enc.encode(entry.name)
    const raw = enc.encode(entry.text)
    const data = entry.method === 8 ? await deflate(raw) : raw
    const local = new Uint8Array(30 + name.length + data.length)
    const lv = new DataView(local.buffer)
    lv.setUint32(0, 0x04034b50, true)
    lv.setUint16(8, entry.method, true)
    lv.setUint32(18, data.length, true)
    lv.setUint32(22, raw.length, true)
    lv.setUint16(26, name.length, true)
    local.set(name, 30)
    local.set(data, 30 + name.length)
    const central = new Uint8Array(46 + name.length)
    const cv = new DataView(central.buffer)
    cv.setUint32(0, 0x02014b50, true)
    cv.setUint16(10, entry.method, true)
    cv.setUint32(20, data.length, true)
    cv.setUint32(24, raw.length, true)
    cv.setUint16(28, name.length, true)
    cv.setUint32(42, offset, true)
    central.set(name, 46)
    locals.push(local)
    centrals.push(central)
    offset += local.length
  }
  const centralSize = centrals.reduce((n, c) => n + c.length, 0)
  const end = new Uint8Array(22)
  const ev = new DataView(end.buffer)
  ev.setUint32(0, 0x06054b50, true)
  ev.setUint16(8, entries.length, true)
  ev.setUint16(10, entries.length, true)
  ev.setUint32(12, centralSize, true)
  ev.setUint32(16, offset, true)
  const out = new Uint8Array(offset + centralSize + 22)
  let at = 0
  for (const part of [...locals, ...centrals, end]) {
    out.set(part, at)
    at += part.length
  }
  return out.buffer as ArrayBuffer
}

const p = (body: string, style = '') =>
  `<w:p>${style ? `<w:pPr><w:pStyle w:val="${style}"/></w:pPr>` : ''}${body}</w:p>`
const r = (text: string) => `<w:r><w:t xml:space="preserve">${text}</w:t></w:r>`

describe('readZipEntry', () => {
  it('存储和 deflate 两种条目都取得出来；没有的那一条给 null', async () => {
    const bytes = await zip([
      { name: 'a.txt', text: '原样存的', method: 0 },
      { name: 'b.txt', text: '压缩过的'.repeat(20), method: 8 },
    ])
    const dec = new TextDecoder()
    expect(dec.decode((await readZipEntry(bytes, 'a.txt'))!)).toBe('原样存的')
    expect(dec.decode((await readZipEntry(bytes, 'b.txt'))!)).toBe('压缩过的'.repeat(20))
    expect(await readZipEntry(bytes, 'c.txt')).toBeNull()
  })

  it('不是 zip 的字节给 null，不抛', async () => {
    expect(await readZipEntry(new TextEncoder().encode('just text, not a zip archive').buffer, 'a')).toBeNull()
  })
})

describe('documentXmlParagraphs', () => {
  it('一段一行；空段不算；制表、换行、实体都还原', () => {
    const xml = `<w:body>${p(r('甲 &amp; 乙') + '<w:r><w:tab/></w:r>' + r('丙'))}${p('')}${p(r('一行') + '<w:r><w:br/></w:r>' + r('二行'))}</w:body>`
    expect(documentXmlParagraphs(xml)).toEqual(['甲 & 乙\t丙', '一行 二行'])
  })

  it('修订里删掉的字不算正文，插入的字算', () => {
    const xml = p(`${r('报名')}<w:del><w:r><w:delText>一周</w:delText></w:r></w:del><w:ins>${r('两天')}</w:ins>`)
    expect(documentXmlParagraphs(xml)).toEqual(['报名两天'])
  })

  it('标题按样式名认（中文版的编号是「1」「2」），列表带编号的标成 - ', () => {
    const names = styleNames(
      '<w:styles><w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>' +
        '<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="Title"/></w:style></w:styles>'
    )
    const xml =
      p(r('招募方案'), 'a3') +
      p(r('一、招募对象'), '1') +
      p(r('二、时间'), 'Heading2') +
      `<w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr>${r('报名截止')}</w:p>` +
      p(r('正文'))
    expect(documentXmlParagraphs(xml, names)).toEqual([
      '# 招募方案',
      '## 一、招募对象',
      '### 二、时间',
      '- 报名截止',
      '正文',
    ])
  })
})

describe('docxParagraphs', () => {
  it('从 .docx 的字节里取正文，样式表一起读', async () => {
    const bytes = await zip([
      { name: 'word/styles.xml', text: '<w:style w:styleId="2"><w:name w:val="heading 2"/></w:style>', method: 8 },
      {
        name: 'word/document.xml',
        text: `<w:document><w:body>${p(r('时间安排'), '2')}${p(r('正文'))}</w:body></w:document>`,
        method: 8,
      },
    ])
    expect(await docxParagraphs(bytes)).toEqual(['### 时间安排', '正文'])
  })

  it('没有正文那一条就是读不开：null', async () => {
    expect(await docxParagraphs(await zip([{ name: 'x.xml', text: '', method: 0 }]))).toBeNull()
  })
})
