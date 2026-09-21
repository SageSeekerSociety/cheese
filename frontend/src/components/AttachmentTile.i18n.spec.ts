/** 输入栏里那一格附件，一句话都不许留在中文上。
 *
 * 这一格自己写的字只有两处：文件名那一行，和 × 的读屏标签（`aria-label`，屏幕上
 * 看不见，只有读屏软件念得到）。没有 readme 那一类的疑问——它是输入栏里最不起眼
 * 的组件，漏翻最容易一直没人发现。
 *
 * 图片和文档那两种形状走 `AttachmentImage` / `AttachmentDocThumb`（一个去要字节、
 * 一个要 pdf.js），不在这份射程里。这里挑一个两者都不是的 mime，让这一格自己把
 * 三种状态画完。
 */
import type { PendingAttachment } from '@/lib/attachments'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import AttachmentTile from './AttachmentTile.vue'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/
/** 一个既不是图片、也不是文档的附件：这一格于是自己画方格、名字和 ×。 */
const PLAIN: PendingAttachment = { path: 'archive/data.zip', mime: 'application/zip' }

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})
afterEach(cleanup)
beforeEach(() => setLocale('zh-CN'))

function tile(over: Partial<PendingAttachment> = {}) {
  return render(AttachmentTile, {
    props: { topicId: 'topic-a', attachment: { ...PLAIN, ...over } },
    global: { plugins: [vuetify] },
  })
}

const nameOf = (c: Element) => c.querySelector('.att-card__name')?.textContent?.trim() ?? ''
const removeOf = (c: Element) => c.querySelector('.att-card__remove')?.getAttribute('aria-label') ?? ''

describe('讲中文', () => {
  it('名字取路径的末一段，× 的读屏标签是中文', () => {
    const { container } = tile()

    expect(nameOf(container)).toBe('data.zip')
    expect(removeOf(container)).toBe('移除')
  })

  // 上传中那一格还没有工作区路径，名字只有它自己记着的那一份。
  it('还在上传时用的是它自己记着的那个名字', () => {
    const { container } = tile({ uploading: true, name: 'report.pdf' })

    expect(nameOf(container)).toBe('report.pdf')
  })
})

describe('讲英文', () => {
  it('整格一个汉字都不剩，读屏标签也是英文', () => {
    setLocale('en')
    const { container } = tile()

    expect(nameOf(container)).toBe('data.zip')
    expect(removeOf(container)).toBe('Remove')
    const said = (container.textContent ?? '').replace(/\s+/g, ' ')
    expect(CJK.test(said), said).toBe(false)
  })

  it('写在外面的 aria-label 也不会漏——它是这一格唯一不落在 textContent 里的字', () => {
    setLocale('en')
    const { container } = tile({ uploading: true, name: 'report.pdf' })

    expect(removeOf(container)).toBe('Remove')
    expect(CJK.test(removeOf(container)), removeOf(container)).toBe(false)
  })
})
