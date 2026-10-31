// @vitest-environment jsdom
/**
 * 页式文档里「这一处属于哪一节」是怎么认的。
 *
 * PDF 的文字层里没有「标题」这个结构，只能按字号认。这组用例钉的是判据本身，重点在
 * **认不出来时不会乱认**：卡上写错一节会把读者指到一个不存在的标题上，而只写页码
 * 永远是准的。所以「正文多的文档里正文不变标题」「并排大小字不算标题」这几条比
 * 「认出来了」更重要。
 */
import { describe, expect, it } from 'vitest'

import { headingsOf, rowsInLayer, sectionAt, type MeasureRow } from './pageSections'

/** 一行字。`size` 是原始字号（不含缩放），和 pdf.js 的 `--font-height` 同一个东西。 */
function row(top: number, text: string, size = 10, height = size * 1.2): MeasureRow {
  return { top, bottom: top + height, text, size }
}

describe('哪一行算标题', () => {
  it('比正文明显大的整行是标题，正文不是', () => {
    const page = [row(0, '报名方案 B 实现与核验', 22), row(40, '这一版修掉了一个窄屏真缺陷。'), row(60, '结论：已实现。')]
    expect(headingsOf([page]).map((h) => h.text)).toEqual(['报名方案 B 实现与核验'])
  })

  it('表格行数远多于正文时，正文也不会被当成标题', () => {
    // 一份报告里表格常常比正文密好几倍；基准若落在表格那号字上，整页正文都会变成「标题」。
    const body = Array.from({ length: 6 }, (_, i) => row(i * 20, `正文第 ${i} 行`, 14))
    const table = Array.from({ length: 40 }, (_, i) => row(400 + i * 16, `表格第 ${i} 行`, 11))
    const found = headingsOf([[...body, ...table, row(2000, '四、出错态文案', 20)]])
    expect(found.map((h) => h.text)).toEqual(['四、出错态文案'])
  })

  it('同一行里并排着一个大字，那一行不是标题', () => {
    // 表格的一格或正文里一句放大的话：字比正文大，但同一行里还站着小字。
    const rows: MeasureRow[] = [
      { ...row(0, '指标卡骨架屏 + 重试', 16), minSize: 10 },
      ...Array.from({ length: 8 }, (_, i) => row(40 + i * 20, `正文第 ${i} 行`, 12)),
    ]
    expect(headingsOf([rows])).toEqual([])
  })

  it('普通正文里一句放大的话不算标题', () => {
    const body = Array.from({ length: 20 }, (_, i) => row(i * 20, `正文第 ${i} 行`, 14))
    const loud = row(500, '注意：这一条很重要，必须做。', 16)
    const found = headingsOf([[...body, loud, row(900, '五、截图', 22)]])
    expect(found.map((h) => h.text)).toEqual(['五、截图'])
  })

  it('长到读不下去的行不当标题', () => {
    const long = '结'.repeat(80)
    expect(headingsOf([[row(0, long, 22), row(40, '正文正文正文正文正文正文')]])).toEqual([])
  })

  it('只有一种字号的文件里认不出任何标题', () => {
    const page = [row(0, '甲'.repeat(10)), row(20, '乙'.repeat(10)), row(40, '丙'.repeat(10))]
    expect(headingsOf([page])).toEqual([])
  })

  it('章节跨页：每页开头一个标题，每页的正文都不进结果', () => {
    const pages: MeasureRow[][] = []
    for (let p = 0; p < 5; p += 1) {
      pages.push([
        row(0, `${p + 1}. 第 ${p + 1} 章`, 22),
        ...Array.from({ length: 10 }, (_, i) => row(60 + i * 20, `第 ${p + 1} 页正文第 ${i} 行`, 14)),
        ...Array.from({ length: 30 }, (_, i) => row(600 + i * 16, `第 ${p + 1} 页表格第 ${i} 行`, 11)),
      ])
    }
    expect(headingsOf(pages)).toHaveLength(5)
  })
})

describe('这一处属于哪一节', () => {
  const headings = [
    { page: 1, top: 10, text: '一、报名', size: 18 },
    { page: 1, top: 200, text: '二、时间安排', size: 18 },
    { page: 3, top: 50, text: '三、面试', size: 18 },
  ]

  it('取它之前最近的那个标题', () => {
    expect(sectionAt(headings, 1, 100)).toBe('一、报名')
    expect(sectionAt(headings, 1, 260)).toBe('二、时间安排')
  })

  it('跨页也算：第 2 页的正文属于第 1 页最后一节', () => {
    expect(sectionAt(headings, 2, 30)).toBe('二、时间安排')
    expect(sectionAt(headings, 4, 0)).toBe('三、面试')
  })

  it('第一个标题之前没有节', () => {
    expect(sectionAt(headings, 1, 0)).toBe('')
  })

  it('标题都在这一处之后时，不写节', () => {
    expect(sectionAt([{ page: 2, top: 10, text: '二、时间安排', size: 18 }], 1, 500)).toBe('')
  })
})

describe('从文字层量行', () => {
  function span(layer: HTMLElement, text: string, box: DOMRect, size: string) {
    const el = document.createElement('span')
    el.textContent = text
    el.style.setProperty('--font-height', size)
    el.getBoundingClientRect = () => box
    layer.append(el)
    return el
  }

  it('字号读的是 --font-height，不是量出来的像素高', () => {
    // 面板缩放会改变量出来的高度，不该改变「哪些行是标题」。
    const layer = document.createElement('div')
    span(layer, '大标题', { top: 0, left: 0, width: 100, height: 30, bottom: 30, right: 100 } as DOMRect, '20')
    span(layer, '正文', { top: 40, left: 0, width: 100, height: 10, bottom: 50, right: 100 } as DOMRect, '10')
    expect(rowsInLayer(layer, { top: 0, left: 0 } as DOMRect).map((r) => r.size)).toEqual([20, 10])
  })

  it('同一行上的几段合成一条，记下这一行里最小的字号', () => {
    const layer = document.createElement('div')
    span(layer, '指标', { top: 0, left: 0, width: 40, height: 10, bottom: 10, right: 40 } as DOMRect, '16')
    span(layer, '卡', { top: 0, left: 40, width: 20, height: 10, bottom: 10, right: 60 } as DOMRect, '10')
    const [merged] = rowsInLayer(layer, { top: 0, left: 0 } as DOMRect)
    expect(merged!.text).toBe('指标卡')
    expect(merged!.size).toBe(16)
    expect(merged!.minSize).toBe(10)
  })
})
