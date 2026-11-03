// What a page made for printing carries: the document's text, the pictures'
// bytes, and the two blocks paper cannot have. The browser that prints it is
// another machine with no credential and no room, so anything missing here is
// missing from the PDF.
import { afterEach, describe, expect, it } from 'vitest'

import { documentPrintHtml } from './print'

import { t } from '@/i18n'

const BODY = '本周完成了三件事。'

afterEach(() => {
  document.body.replaceChildren()
})

describe('a document made into a page for printing', () => {
  it('carries the title and the words the reader sees', async () => {
    const page = await documentPrintHtml({ title: '项目周报', markdown: `# 进展\n\n${BODY}` })

    expect(page).toContain('<title>项目周报</title>')
    expect(page).toContain('项目周报')
    expect(page).toContain(BODY)
    // A4 with the printer's own margins, not the screen's column.
    expect(page).toContain('@page')
    expect(page).toContain('A4')
  })

  it('draws a picture from the bytes it was handed, not from a path only the room can open', async () => {
    const page = await documentPrintHtml({
      title: '图',
      markdown: '![耗时对比](figures/耗时.png)',
      image: async () => 'data:image/png;base64,AAAA',
    })

    expect(page).toContain('src="data:image/png;base64,AAAA"')
    expect(page).not.toContain('figures/耗时.png')
  })

  it('keeps a picture nobody could read as its description, not as an empty frame', async () => {
    const page = await documentPrintHtml({
      title: '图',
      markdown: '![架构](library/架构.png)',
      image: () => Promise.reject(new Error('404')),
    })

    expect(page).not.toContain('<img src=')
    expect(page).toContain('架构')
  })

  it('names a mini-app instead of running it: paper cannot', async () => {
    const page = await documentPrintHtml({
      title: '小程序',
      markdown: ':::app q7m2kx4t@3 并发数对排队时间的影响\n拖动滑块试试看\n:::',
    })

    expect(page).not.toContain('<iframe')
    expect(page).toContain('并发数对排队时间的影响')
    expect(page).toContain(t('work.room.doc.blocks.printApp'))
  })

  it("keeps a video's address, which still opens it", async () => {
    const page = await documentPrintHtml({
      title: '视频',
      markdown: 'https://www.bilibili.com/video/BV1xx411c7mD',
    })

    expect(page).not.toContain('<iframe')
    expect(page).toContain('https://www.bilibili.com/video/BV1xx411c7mD')
  })
})
