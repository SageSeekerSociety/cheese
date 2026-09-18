/** 预览 tab 整块跟着语言走。
 *
 * 这一份真的把面板挂起来渲染一遍，并且把它的每一张脸都走一遍：跑起来的应用（那一格
 * iframe）、什么都没有的空场、拉不到预览、应用掉线（通道在 / 不在）、文档类交付物
 * （markdown 那一支）、这个部署没有转换服务、读不成文本、指定的文件读不到。扫的是
 * `.panel-preview` 整块的 `textContent`，加上它下面所有 `title`（一个字的正文都不
 * 在 textContent 里的那几个按钮，加 iframe 的标题）。
 *
 * 两处**不在这份的射程里**，都记在报告里：
 *
 * - `previewError` / `docError` / `previewReadError` 里那句 `reason` 是**别人给的原话**
 *   （API 报错、转换服务的报错），不是这块面板写的词。这里的用例统一给英文原话，
 *   这样扫出来的汉字只可能是面板自己的漏翻。
 * - `lib/previewSession.ts` 里那句「预览地址未与平台隔离」是 `lib/` 切片的字，而且它
 *   是**抛出来的** Error message，会原样落进 `loadFailedDetail`——那条债是 lib/ 那一
 *   份要还的，这一份不碰，也不假装它不存在。
 * - 定位条（`说明要改什么` / `发送`）要经过 PreviewPages / PreviewSheet 的选中事件才
 *   出得来，中文那两句归 `PanelPreview.document.spec.ts` 钉着，这里不重复搭台。
 */
import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
const downloadFile = vi.fn()
const attachmentRawUrl = vi.fn()
const previewFileBytes = vi.fn()
const previewDocumentPdf = vi.fn()

// `vi.mock` 的工厂会被提到文件里所有语句之上，所以这里用 vi.hoisted 拿这个类。
const { PreviewRendererUnavailable } = vi.hoisted(() => ({
  PreviewRendererUnavailable: class extends Error {},
}))

vi.mock('../../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  readPreviewFile: (...args: unknown[]) => readPreviewFile(...args),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
  downloadFile: (...args: unknown[]) => downloadFile(...args),
  attachmentRawUrl: (...args: unknown[]) => attachmentRawUrl(...args),
  previewFileBytes: (...args: unknown[]) => previewFileBytes(...args),
  previewDocumentPdf: (...args: unknown[]) => previewDocumentPdf(...args),
  PreviewRendererUnavailable,
}))

// 两个查看器要 pdf.js / exceljs，不该出现在这块面板的用例里。这一份碰不到它们
// （markdown 自己渲染，其余几支都在查看器之前就分岔了），但还是挡一下，免得哪一
// 条用例把整包 pdf.js 拖进来。
vi.mock('./preview/PreviewPages.vue', () => ({
  default: { name: 'PreviewPages', props: ['data'], emits: ['quote'], template: '<div data-testid="pages" />' },
}))
vi.mock('./preview/PreviewSheet.vue', () => ({
  default: {
    name: 'PreviewSheet',
    props: ['data', 'kind'],
    emits: ['cell'],
    template: '<div data-testid="sheet" />',
  },
}))

import PanelPreview from './PanelPreview.vue'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/
const APP_URL = 'https://preview-topic-a.example/'

function artifact(over: Partial<PreviewInfo> = {}): PreviewInfo {
  return {
    kind: 'app',
    path: 'Dev server',
    mime: 'text/html',
    url: APP_URL,
    artifact_id: 'artifact-a',
    tunnel_up: true,
    ...over,
  }
}

/** 交付物不进 iframe：预览域按自己的 mime 发字节，面板自己画（`url: null` 就是
 *  这一支的分岔处——有 url 的话会去走上面那条授权 + iframe 的路）。 */
function fileArtifact(path: string, mime: string, over: Partial<PreviewInfo> = {}): PreviewInfo {
  return artifact({ kind: 'file', path, mime, url: null, ...over })
}

let vuetify: ReturnType<typeof createVuetify>

/** 全屏在 happy-dom 里默认不支持，不给的话那个按钮根本不画；会话那一份也是这么
 *  搭的台。 */
const fullscreenDescriptors = [
  [document, 'fullscreenElement'],
  [document, 'fullScreen'],
  [document, 'exitFullscreen'],
  [HTMLElement.prototype, 'requestFullscreen'],
].map(([owner, key]) => ({
  owner: owner as object,
  key: key as string,
  descriptor: Object.getOwnPropertyDescriptor(owner as object, key as string),
}))

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  vi.clearAllMocks()
  for (const [owner, key] of [
    [document, 'fullscreenElement'],
    [document, 'fullScreen'],
    [document, 'exitFullscreen'],
    [HTMLElement.prototype, 'requestFullscreen'],
  ] as [object, string][]) {
    Object.defineProperty(owner, key, { configurable: true, writable: true, value: () => {} })
  }
  getPreview.mockResolvedValue(artifact())
  readPreviewFile.mockResolvedValue({ path: 'report.html', content: '<p>body</p>', version: 'v1' })
  requestPreviewSession.mockResolvedValue({ url: `${APP_URL}_cheese/session`, grant: 'grant' })
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  downloadFile.mockResolvedValue(undefined)
  previewFileBytes.mockResolvedValue(new ArrayBuffer(2048))
  previewDocumentPdf.mockResolvedValue(new ArrayBuffer(4096))
  // 表单提交是「开一个新页面」，在 happy-dom 里没有下一站可去。
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  for (const { owner, key, descriptor } of fullscreenDescriptors) {
    if (descriptor) Object.defineProperty(owner, key, descriptor)
    else Reflect.deleteProperty(owner, key)
  }
})

function mount() {
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [vuetify] },
  })
}

const root = (c: Element) => c.querySelector('.panel-preview') as HTMLElement
const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const titleOf = (c: Element, el: Element) => el.getAttribute('title') ?? ''
function byTitle(c: Element, title: string): Element | undefined {
  return Array.from(root(c).querySelectorAll('[title]')).find((e) => titleOf(c, e) === title)
}
/** 写着一个词的那个按钮 / 链接（交付入口画出来是 <a>，其余是 <button>）。 */
function byText(c: Element, label: string): Element | undefined {
  return Array.from(root(c).querySelectorAll('button, a')).find((e) => text(e) === label)
}
/** 面板自己的字：正文（含 iframe 的标题这种不落 textContent 的属性）+ 所有 title。 */
function assertNoCJK(c: Element) {
  const el = root(c)
  const body = el.textContent ?? ''
  expect(CJK.test(body), `正文里有汉字：${body}`).toBe(false)
  const titles = Array.from(el.querySelectorAll('[title]'))
    .map((e) => e.getAttribute('title') ?? '')
    .join(' | ')
  expect(CJK.test(titles), `title 里有汉字：${titles}`).toBe(false)
}

describe('讲中文', () => {
  it('跑起来的应用：交付入口、状态 chip、几个按钮的 title 都是中文', async () => {
    setLocale('zh-CN')
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('iframe')).not.toBeNull())
    const c = view.container

    expect(byTitle(c, '刷新')).toBeTruthy()
    expect(byText(c, '导出与发布')).toBeTruthy()
    expect(byTitle(c, '在新标签页打开')).toBeTruthy()
    expect(byTitle(c, '全屏预览')).toBeTruthy()
    expect(text(c.querySelector('.preview-bar'))).toContain('Dev server')
    expect(text(c.querySelector('.preview-bar'))).toContain('运行中的应用')
    expect(titleOf(c, c.querySelector('iframe')!)).toBe('话题预览')

    view.unmount()
  })
})

describe('讲英文', () => {
  it('跑起来的应用：整块一个汉字都不剩', async () => {
    setLocale('en')
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('iframe')).not.toBeNull())
    const c = view.container

    expect(byText(c, 'Export & publish')).toBeTruthy()
    expect(byTitle(c, 'Refresh')).toBeTruthy()
    expect(byTitle(c, 'Open in a new tab')).toBeTruthy()
    expect(byTitle(c, 'Fullscreen preview')).toBeTruthy()
    expect(titleOf(c, c.querySelector('iframe')!)).toBe('Topic preview')
    expect(text(c.querySelector('.preview-bar'))).toBe('Dev serverRunning app')
    assertNoCJK(c)

    view.unmount()
  })

  it('什么都没有：空场那句也是英文', async () => {
    setLocale('en')
    getPreview.mockResolvedValue(null)
    const view = mount()
    await waitFor(() => expect(text(view.container.querySelector('.panel-preview'))).toContain('No preview yet'))
    const c = view.container

    expect(text(root(c).querySelector('.text-medium-emphasis'))).toContain('No preview yet')
    expect(text(root(c).querySelector('.text-caption'))).toContain('When Cheese makes something you can look at')
    assertNoCJK(c)

    view.unmount()
  })

  it('拉不到预览：一句整话加一句带原因的说明', async () => {
    setLocale('en')
    getPreview.mockRejectedValue(new Error('the platform is not answering'))
    const view = mount()
    await waitFor(() =>
      expect(text(view.container.querySelector('.panel-preview'))).toContain("Couldn't load the preview")
    )
    const c = view.container

    expect(text(root(c).querySelector('.text-caption'))).toBe(
      "The platform couldn't return this topic's preview: the platform is not answering"
    )
    assertNoCJK(c)

    view.unmount()
  })

  it('应用掉线：通道在 / 不在，是两句话', async () => {
    setLocale('en')
    getPreview.mockResolvedValue(artifact({ url: null, tunnel_up: true }))
    const view = mount()
    await waitFor(() => expect(text(view.container.querySelector('.panel-preview'))).toContain('The app is offline'))
    expect(text(root(view.container).querySelector('.text-caption'))).toContain(
      'nothing is answering on the registered port'
    )
    assertNoCJK(view.container)

    getPreview.mockResolvedValue(artifact({ url: null, tunnel_up: false }))
    await view.rerender({ topicId: 'topic-b', projectId: 'project-a', active: true })
    await waitFor(() =>
      expect(text(root(view.container).querySelector('.text-caption'))).toContain("isn't carrying a preview tunnel")
    )
    assertNoCJK(view.container)

    view.unmount()
  })

  it('markdown 交付物：那一条工具栏和渲染出来的正文', async () => {
    setLocale('en')
    getPreview.mockResolvedValue(fileArtifact('docs/report.md', 'text/markdown'))
    readPreviewFile.mockResolvedValue({ path: 'docs/report.md', content: '# Title\n\nbody text\n', version: 'v1' })
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('[data-testid="markdown"]')).not.toBeNull())
    const c = view.container

    expect(text(c.querySelector('.doc__name'))).toBe('report.md')
    expect(text(c.querySelector('.doc__type'))).toBe('Markdown')
    expect(byText(c, 'Download')).toBeTruthy()
    expect(text(c.querySelector('[data-testid="markdown"]'))).toContain('body text')
    assertNoCJK(c)

    view.unmount()
  })

  it('这个部署没有转换服务：说的是缺服务，不是这个文件转不了', async () => {
    setLocale('en')
    getPreview.mockResolvedValue(
      fileArtifact('output/report.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    )
    // 文档类交付物的正文是字节，读不成文本（`content: null`）—— 这正是它走转换、
    // 而不是落进 iframe 那条路的判据。
    readPreviewFile.mockResolvedValue({
      path: 'output/report.docx',
      content: null,
      version: 'v1',
      bytes: 38705,
      binary: true,
      too_large: false,
    })
    previewDocumentPdf.mockRejectedValue(new PreviewRendererUnavailable('no renderer here'))
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('.doc__state--text')).not.toBeNull())
    const c = view.container

    expect(text(root(c).querySelector('.doc__state--text'))).toBe('Document preview is not enabled')
    expect(byText(c, 'Download')).toBeTruthy()
    assertNoCJK(c)

    view.unmount()
  })

  it('读不成文本的文件：正文、说明和出口都是英文', async () => {
    setLocale('en')
    getPreview.mockResolvedValue(fileArtifact('output/data.bin', 'application/octet-stream'))
    readPreviewFile.mockResolvedValue({ path: 'output/data.bin', content: null, version: 'v1', bytes: 90 })
    const view = mount()
    await waitFor(() => expect(text(view.container.querySelector('.panel-preview'))).toContain("This file isn't text"))
    const c = view.container

    expect(text(c.querySelector('.text-caption'))).toBe(
      "output/data.bin can't be shown as a web page — open it in a new window"
    )
    expect(byText(c, 'Open in a new window')).toBeTruthy()
    assertNoCJK(c)

    view.unmount()
  })

  it('芝士指定的文件读不到：那句整话里带着路径', async () => {
    setLocale('en')
    getPreview.mockResolvedValue(fileArtifact('output/report.html', 'text/html'))
    readPreviewFile.mockRejectedValue(new Error('gone'))
    const view = mount()
    await waitFor(() =>
      expect(text(view.container.querySelector('.panel-preview'))).toContain("The named file can't be read")
    )
    const c = view.container

    expect(text(c.querySelector('.text-caption'))).toBe(
      "Cheese named output/report.html, but it can't be read right now: gone"
    )
    assertNoCJK(c)

    view.unmount()
  })
})

describe('切一次语言', () => {
  it('已经画出来的那一格当场跟着换', async () => {
    setLocale('zh-CN')
    const view = mount()
    await waitFor(() => expect(view.container.querySelector('iframe')).not.toBeNull())
    expect(titleOf(view.container, view.container.querySelector('iframe')!)).toBe('话题预览')

    setLocale('en')
    await waitFor(() => expect(titleOf(view.container, view.container.querySelector('iframe')!)).toBe('Topic preview'))
    expect(byTitle(view.container, 'Refresh')).toBeTruthy()
    expect(byTitle(view.container, 'Open in a new tab')).toBeTruthy()
    expect(text(view.container.querySelector('.preview-bar'))).toBe('Dev serverRunning app')
    assertNoCJK(view.container)

    view.unmount()
  })
})
