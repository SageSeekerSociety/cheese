/** 文档 tab 整块跟着语言走。
 *
 * 这一份真的把这格挂起来渲染一遍（tiptap 编辑器一起建）：没选话题时的空场、工具条
 * 上的「只读 / 源码」、文档标题、以及「这篇文档编辑器吃不下」那条横幅。扫的是
 * `.doc` 整块的 `textContent` 加上所有 `title`。
 *
 * 两处**摘掉**的，都是别人的字或别人的数据，不是这块面板漏翻的词：
 *
 * - `.doc-editor`（ProseMirror 正文）是**文档内容本身** —— 读者和芝士写进去的东西，
 *   它是什么语言由文档决定，不由界面语言决定。
 * - `.doc-comments`（`doc/DocComments.vue`）和 `.v-dialog`（源码模式、破坏性确认那
 *   几个弹窗由别的切片管）是另外的文件，它们自己的中文这一轮不归这块面板还。摘掉的
 *   区域在这里点名为的是让「摘掉」看得见：这一份扫的是 `.doc` 里剩下的每一处字。
 *
 * 「未保存的改动被暂存」「磁盘上的版本更新了」那两条 notice 都要先把编辑器改脏、
 * 再让服务端换一版才出得来（`lib/docEditState.ts` 的状态机在它自己的单测里钉着），
 * 这里不搭那个台；它们进词表之后由 catalog.spec.ts 的「en 里没有汉字」守着。
 */
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDoc = vi.fn()
const getComments = vi.fn()
const getDocNodes = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    getComments: (...a: unknown[]) => getComments(...a),
    getDocNodes: (...a: unknown[]) => getDocNodes(...a),
  }
})

// Monaco 起不来（也不是这份要考的东西）。
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template: '<textarea class="stub-editor" />',
  },
}))

import PanelDoc from './PanelDoc.vue'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 'Topic A',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

function doc(content: string): Block {
  return { id: 'd1', kind: 'doc', content, doc_version: 3 } as unknown as Block
}

/** 编辑器吃不下的一段：setext 标题会被改写成 ATX，于是这一篇是 lossy 的。 */
const LOSSY = 'Title\n===\n\nBody\n'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  vi.clearAllMocks()
  getDoc.mockResolvedValue(doc('# Heading\n\nBody\n'))
  getComments.mockResolvedValue({ data: [], total: 0 })
  getDocNodes.mockResolvedValue({ data: [], total: 0 })
})

function mount(withTopic: Topic | null) {
  return render(PanelDoc, {
    props: { topic: withTopic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify] },
  })
}

/** 等文档到齐（正文进了编辑器）。 */
async function open(withTopic: Topic | null = topic) {
  const view = mount(withTopic)
  if (withTopic) await waitFor(() => expect(view.container.querySelector('.doc-editor')).not.toBeNull())
  return view
}

const root = (c: Element) => c.querySelector('.doc') as HTMLElement
const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()

/** `.doc` 里**不属于这块面板**的那几块：正文编辑器（文档内容本身）、别人的组件、
 *  被 teleport 走的弹窗。摘掉再扫。 */
const OTHERS = '.doc-editor, .doc-comments, .v-dialog, .doc-skel'

/** 面板自己写的字：剩下的 textContent，和剩下的所有 title。 */
function ownText(c: Element): string {
  const copy = root(c).cloneNode(true) as HTMLElement
  copy.querySelectorAll(OTHERS).forEach((n) => n.remove())
  return copy.textContent ?? ''
}
function ownTitles(c: Element): string {
  const copy = root(c).cloneNode(true) as HTMLElement
  copy.querySelectorAll(OTHERS).forEach((n) => n.remove())
  return Array.from(copy.querySelectorAll('[title]'))
    .map((e) => e.getAttribute('title') ?? '')
    .join(' | ')
}
function assertNoCJK(c: Element) {
  const own = ownText(c)
  expect(CJK.test(own), `正文里有汉字：${own}`).toBe(false)
  const titles = ownTitles(c)
  expect(CJK.test(titles), `title 里有汉字：${titles}`).toBe(false)
}
function byText(c: Element, label: string): Element | undefined {
  return Array.from(root(c).querySelectorAll('button, a')).find((e) => text(e) === label)
}

describe('讲中文', () => {
  it('没选话题：那句空场是中文', async () => {
    setLocale('zh-CN')
    const view = await open(null)

    expect(text(root(view.container))).toContain('选择一个话题查看文档')

    view.unmount()
  })

  it('文档到了：工具条上的「只读 / 源码」和文档标题', async () => {
    setLocale('zh-CN')
    const view = await open()
    const c = view.container

    expect(text(c.querySelector('.doc-page__title'))).toBe('Topic A')
    expect(byText(c, '只读')).toBeTruthy()
    expect(byText(c, '源码')).toBeTruthy()
    expect(byText(c, '源码')?.getAttribute('title')).toBe('源码模式（直接编辑 markdown 原文）')

    view.unmount()
  })
})

describe('讲英文', () => {
  it('没选话题：整块一个汉字都不剩', async () => {
    setLocale('en')
    const view = await open(null)

    expect(text(root(view.container))).toBe('Pick a topic to see its document')
    assertNoCJK(view.container)

    view.unmount()
  })

  it('文档到了：工具条和标题都是英文，正文和评论区不在这份的射程里', async () => {
    setLocale('en')
    const view = await open()
    const c = view.container

    expect(text(c.querySelector('.doc-page__title'))).toBe('Topic A')
    expect(byText(c, 'Read-only')).toBeTruthy()
    expect(byText(c, 'Source')?.getAttribute('title')).toBe('Source mode (edit the raw markdown)')
    // 正文（`.doc-editor`）是文档内容本身，跳过；工具条那半句在。
    expect(text(c.querySelector('.doc-bar'))).toBe('Read-onlySource')
    assertNoCJK(c)

    view.unmount()
  })

  it('编辑器吃不下的一篇：横幅和「用源码模式」都是英文', async () => {
    setLocale('en')
    getDoc.mockResolvedValue(doc(LOSSY))
    const view = await open()
    const c = view.container

    expect(text(c.querySelector('.doc-lossy-banner'))).toContain(
      "This document uses syntax the editor doesn't fully support"
    )
    expect(byText(c, 'Source mode')).toBeTruthy()
    assertNoCJK(c)

    view.unmount()
  })
})

describe('切一次语言', () => {
  it('已经画出来的工具条和横幅当场跟着换', async () => {
    setLocale('zh-CN')
    getDoc.mockResolvedValue(doc(LOSSY))
    const view = await open()
    const c = view.container
    expect(byText(c, '只读')).toBeTruthy()
    expect(text(c.querySelector('.doc-lossy-banner'))).toContain('此文档包含编辑器暂不完全支持的语法')

    setLocale('en')
    await waitFor(() => expect(byText(c, 'Read-only')).toBeTruthy())
    expect(byText(c, 'Source')).toBeTruthy()
    expect(text(c.querySelector('.doc-lossy-banner'))).toContain(
      "This document uses syntax the editor doesn't fully support"
    )
    assertNoCJK(c)

    view.unmount()
  })
})
