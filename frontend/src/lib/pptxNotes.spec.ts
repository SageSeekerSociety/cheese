/**
 * 备注是另一个部件、而且编号和页序不是一回事，所以这组测试的重点是「谁属于哪一页」：
 * 一份真的 zip，六页里三页有备注，其中两页的备注部件编号**故意乱序**（第 2 页用
 * notesSlide3.xml），一页的备注页上带着页码框。走的是 `readZipEntry` 的完整路径。
 */
import { describe, expect, it } from 'vitest'

import { notesSlideText, pptxNotes, relationshipTargets, relsPathOf, slideRelIds } from './pptxNotes'
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

// ---- 部件 ----

const rel = (id: string, target: string, type: string) =>
  `<Relationship Id="${id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/${type}" Target="${target}"/>`
const rels = (...rows: string[]) =>
  `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${rows.join('')}</Relationships>`

const presentation = (...ids: string[]) =>
  `<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
  `<p:sldIdLst>${ids.map((id, i) => `<p:sldId id="${256 + i}" r:id="${id}"/>`).join('')}</p:sldIdLst></p:presentation>`

/** 一个形状；给了 `ph` 就带上占位符类型。属性名照 PowerPoint 写的 `type`。 */
const shape = (text: string, ph?: string, ...more: string[]) =>
  `<p:sp><p:nvSpPr><p:nvPr>${ph ? `<p:ph type="${ph}"/>` : ''}</p:nvPr></p:nvSpPr>` +
  [text, ...more].map((line) => `<a:p><a:r><a:t>${line}</a:t></a:r></a:p>`).join('') +
  `</p:sp>`

const notesSlide = (...shapes: string[]) =>
  `<?xml version="1.0"?><p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">` +
  `<p:cSld><p:spTree>${shapes.join('')}</p:spTree></p:cSld></p:notes>`

describe('relationshipTargets', () => {
  it('相对归属部件展开：presentation 那边的 slides/、幻灯片那边的 ../notesSlides/ 都归一', () => {
    expect(relationshipTargets(rels(rel('rId1', 'slides/slide1.xml', 'slide')), 'ppt/presentation.xml').get('rId1')).toBe(
      'ppt/slides/slide1.xml'
    )
    // 备注那条关系住在幻灯片旁边，所以 `..` 是从 `ppt/slides/` 往上一级。
    expect(
      relationshipTargets(rels(rel('n1', '../notesSlides/notesSlide1.xml', 'notesSlide')), 'ppt/slides/slide1.xml').get('n1')
    ).toBe('ppt/notesSlides/notesSlide1.xml')
  })
})

describe('relsPathOf', () => {
  it('把部件路径换成它旁边的 _rels', () => {
    expect(relsPathOf('ppt/slides/slide12.xml')).toBe('ppt/slides/_rels/slide12.xml.rels')
  })
})

describe('slideRelIds', () => {
  it('按放映顺序取关系 id', () => {
    expect(slideRelIds(presentation('rId2', 'rId9', 'rId3'))).toEqual(['rId2', 'rId9', 'rId3'])
  })
})

describe('notesSlideText', () => {
  it('一段一行；页码框和页脚、日期的框不算备注', () => {
    const xml = notesSlide(
      shape('7', 'sldNum'),
      shape('先讲结论，再展开时间节点。', undefined, '预算那一页留 30 秒。'),
      shape('2026-10-28', 'dt'),
      shape('招新方案', 'ftr')
    )
    expect(notesSlideText(xml)).toBe('先讲结论，再展开时间节点。\n预算那一页留 30 秒。')
  })

  it('没有 type 的占位符是正文，照常算', () => {
    expect(notesSlideText(notesSlide(shape('正文占位符里的备注')))).toBe('正文占位符里的备注')
  })

  it('实体还原，空段不算', () => {
    expect(notesSlideText(notesSlide(shape('甲 &amp; 乙'), shape(''), shape('丙')))).toBe('甲 & 乙\n丙')
  })

  it('备注页上没有备注时给空串', () => {
    expect(notesSlideText(notesSlide(shape('1', 'sldNum')))).toBe('')
  })
})

describe('pptxNotes', () => {
  /** 六页；第 2 页的备注住在 notesSlide3.xml、第 5 页住在 notesSlide2.xml —— 编号和页序无关。 */
  async function deck() {
    return zip([
      {
        name: 'ppt/presentation.xml',
        text: presentation('rId1', 'rId2', 'rId3', 'rId4', 'rId5', 'rId6'),
        method: 0,
      },
      {
        name: 'ppt/_rels/presentation.xml.rels',
        text: rels(
          rel('rId1', 'slides/slide1.xml', 'slide'),
          rel('rId2', 'slides/slide2.xml', 'slide'),
          rel('rId3', 'slides/slide3.xml', 'slide'),
          rel('rId4', 'slides/slide4.xml', 'slide'),
          rel('rId5', 'slides/slide5.xml', 'slide'),
          rel('rId6', 'slides/slide6.xml', 'slide')
        ),
        method: 0,
      },
      {
        name: 'ppt/slides/_rels/slide1.xml.rels',
        text: rels(rel('n1', '../notesSlides/notesSlide1.xml', 'notesSlide')),
        method: 0,
      },
      {
        name: 'ppt/slides/_rels/slide2.xml.rels',
        text: rels(rel('n3', '../notesSlides/notesSlide3.xml', 'notesSlide')),
        method: 0,
      },
      { name: 'ppt/slides/_rels/slide3.xml.rels', text: rels(), method: 0 },
      { name: 'ppt/slides/_rels/slide4.xml.rels', text: rels(), method: 0 },
      {
        name: 'ppt/slides/_rels/slide5.xml.rels',
        text: rels(rel('n2', '../notesSlides/notesSlide2.xml', 'notesSlide')),
        method: 0,
      },
      { name: 'ppt/slides/_rels/slide6.xml.rels', text: rels(), method: 0 },
      {
        name: 'ppt/notesSlides/notesSlide1.xml',
        text: notesSlide(shape('2', 'sldNum'), shape('这一页先讲结论。')),
        method: 8,
      },
      {
        name: 'ppt/notesSlides/notesSlide3.xml',
        text: notesSlide(shape('3', 'sldNum'), shape('时间节点那一段要停一下。')),
        method: 8,
      },
      {
        name: 'ppt/notesSlides/notesSlide2.xml',
        text: notesSlide(shape('5', 'sldNum'), shape('收尾，留提问时间。')),
        method: 0,
      },
    ])
  }

  it('按页序给备注，编号乱序也不串页；没有备注的页给空串', async () => {
    expect(await pptxNotes(await deck())).toEqual([
      '这一页先讲结论。',
      '时间节点那一段要停一下。',
      '',
      '',
      '收尾，留提问时间。',
      '',
    ])
  })

  it('不是 pptx（读不开 zip / 缺 presentation）给 null', async () => {
    expect(await pptxNotes(new TextEncoder().encode('just text').buffer)).toBeNull()
    expect(await pptxNotes(await zip([{ name: 'ppt/slides/slide1.xml', text: '<p:sld/>', method: 0 }]))).toBeNull()
  })

  it('缺某一页自己的关系部件时只有那一页空，别的照常', async () => {
    const bytes = await zip([
      { name: 'ppt/presentation.xml', text: presentation('rId1', 'rId2'), method: 0 },
      {
        name: 'ppt/_rels/presentation.xml.rels',
        text: rels(rel('rId1', 'slides/slide1.xml', 'slide'), rel('rId2', 'slides/slide2.xml', 'slide')),
        method: 0,
      },
      { name: 'ppt/slides/_rels/slide2.xml.rels', text: rels(rel('n1', '../notesSlides/notesSlide1.xml', 'notesSlide')), method: 0 },
      { name: 'ppt/notesSlides/notesSlide1.xml', text: notesSlide(shape('第二页的备注')), method: 0 },
    ])
    expect(await pptxNotes(bytes)).toEqual(['', '第二页的备注'])
  })

  it('取的部件真是 zip 里的那一条（不是靠名字猜）', async () => {
    const bytes = await deck()
    expect(new TextDecoder().decode((await readZipEntry(bytes, 'ppt/notesSlides/notesSlide3.xml'))!)).toContain('时间节点')
  })
})
