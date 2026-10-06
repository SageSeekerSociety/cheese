import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
vi.mock('../../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  readPreviewFile: (...args: unknown[]) => readPreviewFile(...args),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
}))

import PanelPreviewHost from '@/components/work/PanelPreviewHost.vue'

const url = 'https://preview-topic-a.example/'
const artifact = (kind: 'app' | 'file' = 'app', id = 'artifact-a'): PreviewInfo => ({
  kind,
  path: kind === 'app' ? 'Dev server' : 'report.html',
  mime: 'text/html',
  url,
  artifact_id: id,
  tunnel_up: true,
})
let submissions: { action: string; target: string; body: string }[]
function opaqueNavigation(frame: Element) {
  Object.defineProperty(frame, 'contentDocument', { configurable: true, get: () => null })
  frame.dispatchEvent(new Event('load'))
}
const fullscreenDescriptors = [
  [document, 'fullscreenElement'],
  [document, 'fullScreen'],
  [document, 'exitFullscreen'],
  [HTMLElement.prototype, 'requestFullscreen'],
].map(([owner, key]) => ({
  owner: owner as object,
  key: key as string,
  descriptor: Object.getOwnPropertyDescriptor(owner, key as string),
}))

function mount() {
  return render(PanelPreviewHost, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 自由区的一个页签：这一格只看房间里的这一份文件。 */
function mountFile(path: string) {
  return render(PanelPreviewHost, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true, path },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  submissions = []
  getPreview.mockResolvedValue(artifact())
  readPreviewFile.mockResolvedValue({ path: 'report.html', content: '<script>arbitrary()</script>' })
  requestPreviewSession.mockResolvedValue({ url: `${url}_cheese/session`, grant: 'preview-grant' })
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    const target = document.querySelector(`iframe[name="${this.target}"]`)!
    expect(target).toBeTruthy()
    queueMicrotask(() => opaqueNavigation(target))
    submissions.push({
      action: this.action,
      target: this.target,
      body: new URLSearchParams(new FormData(this) as never).toString(),
    })
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  for (const { owner, key, descriptor } of fullscreenDescriptors) {
    if (descriptor) Object.defineProperty(owner, key, descriptor)
    else Reflect.deleteProperty(owner, key)
  }
})

it.each(['app', 'file'] as const)('opens %s with only a grant in a form targeting one isolated frame', async (kind) => {
  getPreview.mockResolvedValue(artifact(kind))
  const { container } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')!
  expect(submissions[0]).toEqual({ action: `${url}_cheese/session`, target: frame.name, body: 'grant=preview-grant' })
  expect(frame.hasAttribute('srcdoc')).toBe(false)
  expect(frame.hasAttribute('src')).toBe(false)
  expect(frame.getAttribute('sandbox')).toContain('allow-same-origin')
  // 页面里点开新标签页的外链要真的开得出去。
  expect(frame.getAttribute('sandbox')).toContain('allow-popups')
  expect(document.querySelector('form')).toBeNull()
})

it('shows failed authorization without mounting an old platform URL', async () => {
  requestPreviewSession.mockRejectedValue(new Error('预览授权失败'))
  const { container, findByText } = mount()
  expect(await findByText(/预览授权失败/)).toBeTruthy()
  expect(container.querySelector('iframe')).toBeNull()
  expect(submissions).toHaveLength(0)
})

it.each(['site/index.html', '图.svg'])('draws the room file %s in the sandboxed frame, not as source', async (path) => {
  readPreviewFile.mockResolvedValue({
    path,
    content: '<h1>源码不该被画出来</h1>',
    version: 'v1',
    bytes: 20,
    binary: false,
    too_large: false,
  })
  const { container } = mountFile(path)
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')!
  // 房间文件不挂在 artifact 下面，所以它带着自己的地址过去——内容域按那个地址取字节，
  // 页面里的相对资源也就落在同一份文件旁边。
  expect(submissions[0].action).toBe(`${url}_cheese/session`)
  expect(submissions[0].target).toBe(frame.name)
  expect(new URLSearchParams(submissions[0].body).get('path')).toBe(
    '/_cheese/room/' + path.split('/').map(encodeURIComponent).join('/')
  )
  expect(frame.getAttribute('sandbox')).toBe(
    'allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox'
  )
  // 源码交给 iframe，不是渲染进面板 DOM 里。
  expect(container.textContent).not.toContain('源码不该被画出来')
})

it('escapes a room path with a space and CJK before it becomes an address', async () => {
  mountFile('成品 终稿.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  expect(new URLSearchParams(submissions[0].body).get('path')).toBe(
    '/_cheese/room/%E6%88%90%E5%93%81%20%E7%BB%88%E7%A8%BF.html'
  )
})

it('gives a room file the browser cannot draw a real new-window address', async () => {
  readPreviewFile.mockResolvedValue({
    path: '权重.bin',
    content: null,
    version: 'v1',
    bytes: 2048,
    binary: true,
    too_large: false,
  })
  const open = vi.fn()
  vi.stubGlobal('open', open)
  const { container, findByText, getByText } = mountFile('权重.bin')

  expect(await findByText('这个文件不是文本')).toBeTruthy()
  expect(container.querySelector('iframe')).toBeNull()
  // 那句话承诺了一个动作，所以这一格必须有那个动作——带着它自己的地址。
  await fireEvent.click(getByText('在新窗口打开'))
  const [target, target_, features] = open.mock.calls[0]
  const location = new URL(String(target), 'https://app.example')
  expect(location.pathname).toBe('/previews/topic-a')
  // 查询串里再编一层是 URL 自己的要求；解开之后正好是内容域上这一份的地址。
  expect(location.searchParams.get('path')).toBe('/_cheese/room/%E6%9D%83%E9%87%8D.bin')
  expect([target_, features]).toEqual(['_blank', 'noopener'])
})

it('shows the unsupported file and its own launch target after a loaded app', async () => {
  const opened = vi.spyOn(window, 'open').mockReturnValue(null)
  const { container, rerender, findByText, getByText } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  await waitFor(() => expect(container.textContent).toContain('Dev server'))
  getPreview.mockResolvedValue({
    ...artifact('file', 'binary-b'),
    path: 'weights.bin',
    mime: 'application/octet-stream',
    version: 'v2',
  })
  readPreviewFile.mockResolvedValue({
    path: 'weights.bin',
    content: null,
    version: 'v2',
    bytes: 2048,
    binary: true,
    too_large: false,
  })
  await rerender({ refreshTick: 1 })
  expect(await findByText('这个文件不是文本')).toBeTruthy()
  expect(container.textContent).toContain('weights.bin')
  expect(container.textContent).not.toContain('Dev server')
  expect(container.querySelector('iframe')).toBeNull()
  expect(submissions).toHaveLength(1)
  await fireEvent.click(getByText('在新窗口打开'))
  const [target, target_, features] = opened.mock.calls[0]
  const destination = new URL(String(target), 'https://app.example')
  expect(destination.pathname).toBe('/previews/topic-a')
  expect(destination.searchParams.get('path')).toBe('/_cheese/room/weights.bin')
  expect([target_, features]).toEqual(['_blank', 'noopener'])
})

it('rejects a misconfigured same-origin authorization destination', async () => {
  requestPreviewSession.mockResolvedValue({ url: `${location.origin}/_cheese/session`, grant: 'preview-grant' })
  const { container, findByText } = mount()
  expect(await findByText(/预览地址未与平台隔离/)).toBeTruthy()
  expect(container.querySelector('iframe')).toBeNull()
  expect(submissions).toHaveLength(0)
})

it('ignores a late grant after the panel is gone (a topic switch rebuilds it)', async () => {
  let finish: (value: { url: string; grant: string }) => void = () => {}
  requestPreviewSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { unmount } = mount()
  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalledWith('topic-a', undefined))
  unmount()
  finish({ url: `${url}_cheese/session`, grant: 'old-topic-grant' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submissions).toHaveLength(0)
})

it('ignores a late file read after the panel is gone (a topic switch rebuilds it)', async () => {
  let finish: (value: { path: string; content: string }) => void = () => {}
  getPreview.mockResolvedValue(artifact('file'))
  readPreviewFile.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { unmount } = mount()
  await waitFor(() => expect(readPreviewFile).toHaveBeenCalled())
  unmount()
  finish({ path: 'old-secret.html', content: '<p>old topic</p>' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submissions).toHaveLength(0)
})

it('preserves the document for unchanged metadata and authorizes manual refresh and new artifacts', async () => {
  const { container, rerender, getByTitle } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(getPreview).toHaveBeenCalledTimes(2))
  expect(requestPreviewSession).toHaveBeenCalledTimes(1)
  expect(container.querySelector('iframe')).toBe(frame)
  await fireEvent.click(getByTitle('刷新'))
  await waitFor(() => expect(submissions).toHaveLength(2))
  expect(container.querySelector('iframe')).not.toBe(frame)
  getPreview.mockResolvedValue(artifact('file', 'artifact-b'))
  await rerender({ refreshTick: 2 })
  await waitFor(() => expect(submissions).toHaveLength(3))
  expect(container.querySelector('iframe')).not.toBe(frame)
})

it('opens the platform preview launch route in a new tab', async () => {
  const opened = vi.spyOn(window, 'open').mockReturnValue(null)
  const { getByTitle } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  await fireEvent.click(getByTitle('在新标签页打开最新预览'))
  expect(opened).toHaveBeenCalledWith('/previews/topic-a', '_blank', 'noopener')
})

it('labels the default launch as latest when a failed static target retains a live app', async () => {
  const opened = vi.spyOn(window, 'open').mockReturnValue(null)
  const { container, rerender, getByTitle, findByRole } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const retained = container.querySelector('iframe')
  await fireEvent.click(getByTitle('在新标签页打开最新预览'))
  expect(opened).toHaveBeenLastCalledWith('/previews/topic-a', '_blank', 'noopener')
  getPreview.mockResolvedValue({ ...artifact('file', 'artifact-b'), path: 'b.html', version: 'v2' })
  readPreviewFile.mockResolvedValue({ path: 'b.html', content: '<p>B</p>', version: 'v2' })
  requestPreviewSession.mockRejectedValueOnce(new Error('denied-static-b'))
  await rerender({ refreshTick: 1 })
  expect((await findByRole('alert')).textContent).toContain('denied-static-b')
  expect(container.querySelector('iframe')).toBe(retained)
  expect(container.textContent).toContain('Dev server')
  expect(container.textContent).not.toContain('b.html')
  expect(container.querySelector('[title="在新标签页打开当前连接的应用"]')).toBeNull()
  await fireEvent.click(getByTitle('在新标签页打开最新预览'))
  expect(opened).toHaveBeenLastCalledWith('/previews/topic-a', '_blank', 'noopener')
})

it('opens the displayed static path after the latest target fails', async () => {
  getPreview.mockResolvedValue({ ...artifact('file'), path: 'a.html', version: 'v1' })
  readPreviewFile.mockResolvedValue({ path: 'a.html', content: '<p>A</p>', version: 'v1' })
  const { container, rerender, getByTitle, findByRole } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const retained = container.querySelector('iframe')
  getPreview.mockResolvedValue({ ...artifact('file', 'artifact-b'), path: 'b.html', version: 'v2' })
  readPreviewFile.mockResolvedValue({ path: 'b.html', content: '<p>B</p>', version: 'v2' })
  requestPreviewSession.mockRejectedValueOnce(new Error('denied-b'))
  await rerender({ refreshTick: 1 })
  await findByRole('alert')
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  await fireEvent.click(getByTitle('在新标签页打开'))
  const launched = new URL(String(open.mock.calls[0][0]), 'https://app.example')
  expect(launched.searchParams.get('path')).toBe('/_cheese/room/a.html')
  expect(container.querySelector('iframe')).toBe(retained)
})

it('uses the same frame while entering fullscreen, refreshing and exiting', async () => {
  let current: Element | null = null
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => current })
  Object.defineProperty(document, 'fullScreen', { configurable: true, get: () => !!current })
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
    configurable: true,
    value: vi.fn(async function (this: HTMLElement) {
      // eslint-disable-next-line @typescript-eslint/no-this-alias -- Model the browser's fullscreenElement.
      current = this
      document.dispatchEvent(new Event('fullscreenchange'))
    }),
  })
  Object.defineProperty(document, 'exitFullscreen', {
    configurable: true,
    value: vi.fn(async () => {
      current = null
      document.dispatchEvent(new Event('fullscreenchange'))
    }),
  })
  const { container, getByTitle } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')
  await fireEvent.click(getByTitle('全屏预览'))
  expect(current).toBe(container.querySelector('.panel-preview'))
  expect(container.querySelectorAll('iframe')).toHaveLength(1)
  expect(container.querySelector('iframe')).toBe(frame)
  await fireEvent.click(getByTitle('刷新'))
  await waitFor(() => expect(submissions).toHaveLength(2))
  await fireEvent.click(getByTitle('退出全屏'))
  expect(current).toBeNull()
  expect(container.querySelector('iframe')).not.toBe(frame)
})

it('does not let background metadata cancel an explicit refresh grant', async () => {
  const { rerender, getByTitle } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  let finish: (value: { url: string; grant: string }) => void = () => {}
  requestPreviewSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  await fireEvent.click(getByTitle('刷新'))
  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalledTimes(2))
  await rerender({ refreshTick: 1 })
  finish({ url: `${url}_cheese/session`, grant: 'manual-refresh-grant' })
  await waitFor(() => expect(submissions).toHaveLength(2))
  expect(submissions[1].body).toBe('grant=manual-refresh-grant')
})

it('keeps the displayed v1 context and announces failed v2 authorization', async () => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>one</p>', version: 'v1' })
  const { container, rerender, findByRole } = mountFile('site/index.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  const oldFrame = container.querySelector('iframe')
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>two</p>', version: 'v2' })
  requestPreviewSession.mockRejectedValueOnce(new Error('授权被拒绝'))
  await rerender({ refreshTick: 1 })
  const alert = await findByRole('alert')
  expect(alert.textContent).toContain('授权被拒绝')
  expect(alert.textContent).toContain('当前仍显示上次打开的页面')
  expect(container.querySelector('iframe')).toBe(oldFrame)
  expect(container.textContent).toContain('v1')
  expect(container.textContent).not.toContain('v2')
  expect(submissions).toHaveLength(1)
})

it('performs a true manual navigation for unchanged named HTML', async () => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>same</p>', version: 'v1' })
  const { container, getByTitle } = mountFile('site/index.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  const oldFrame = container.querySelector('iframe')
  await fireEvent.click(getByTitle('刷新'))
  await waitFor(() => expect(submissions).toHaveLength(2))
  expect(container.querySelector('iframe')).not.toBe(oldFrame)
})

// Independent reviewer regressions, reproduced through the owning component.
it('retries canceled pending v2 after reactivating a pane displaying v1', async () => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>one</p>', version: 'v1' })
  const { container, rerender } = mountFile('site/index.html')
  await waitFor(() => expect(container.textContent).toContain('v1'))
  vi.mocked(HTMLFormElement.prototype.submit).mockImplementation(function (this: HTMLFormElement) {
    submissions.push({ action: this.action, target: this.target, body: '' })
  })
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>two</p>', version: 'v2' })
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(submissions).toHaveLength(2))
  const canceledFrame = container.querySelector(`iframe[name="${submissions[1].target}"]`)!
  expect(container.querySelectorAll('iframe')).toHaveLength(2)
  await rerender({ active: false })
  expect(container.querySelectorAll('iframe')).toHaveLength(1)
  opaqueNavigation(canceledFrame)
  await rerender({ active: true })
  await waitFor(() => expect(submissions).toHaveLength(3))
  expect(container.textContent).toContain('v1')
  opaqueNavigation(container.querySelector(`iframe[name="${submissions[2].target}"]`)!)
  await waitFor(() => expect(container.textContent).toContain('v2'))
})

it('exposes app unavailability while retaining a previously displayed page', async () => {
  const { container, rerender, findByRole } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const oldFrame = container.querySelector('iframe')
  getPreview.mockResolvedValue({ ...artifact('app'), url: null, tunnel_up: false })
  await rerender({ refreshTick: 1 })
  const alert = await findByRole('alert')
  expect(alert.textContent).toContain('应用预览暂不可用')
  expect(alert.textContent).toContain('当前仍显示上次打开的页面')
  expect(container.querySelector('iframe')).toBe(oldFrame)
  expect(container.textContent).not.toContain('运行中的应用')
})

it.each(['app', 'file'] as const)(
  'keeps discovering new %s metadata after a failed target without retrying it automatically',
  async (kind) => {
    getPreview.mockResolvedValue({ ...artifact(kind), version: 'v1' })
    const { container, rerender, findByRole } = mount()
    await waitFor(() => expect(submissions).toHaveLength(1))
    getPreview.mockResolvedValue({ ...artifact(kind, 'artifact-b'), version: 'v2' })
    requestPreviewSession.mockRejectedValueOnce(new Error('denied-v2'))
    await rerender({ refreshTick: 1 })
    expect((await findByRole('alert')).textContent).toContain('denied-v2')
    await rerender({ refreshTick: 2 })
    await waitFor(() => expect(getPreview).toHaveBeenCalledTimes(3))
    expect(requestPreviewSession).toHaveBeenCalledTimes(2)
    getPreview.mockResolvedValue({ ...artifact(kind, 'artifact-c'), version: 'v3' })
    await rerender({ refreshTick: 3 })
    await waitFor(() => expect(submissions).toHaveLength(2))
    await waitFor(() => expect(container.textContent).toContain('v3'))
  }
)

it('keeps reading named metadata after failure and navigates a new version', async () => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: 'one', version: 'v1' })
  const { rerender, findByRole, container } = mountFile('site/index.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: 'two', version: 'v2' })
  requestPreviewSession.mockRejectedValueOnce(new Error('denied-v2'))
  await rerender({ refreshTick: 1 })
  await findByRole('alert')
  await rerender({ refreshTick: 2 })
  await waitFor(() => expect(readPreviewFile).toHaveBeenCalledTimes(3))
  expect(requestPreviewSession).toHaveBeenCalledTimes(2)
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: 'three', version: 'v3' })
  await rerender({ refreshTick: 3 })
  await waitFor(() => expect(submissions).toHaveLength(2))
  await waitFor(() => expect(container.textContent).toContain('v3'))
})

it('waits for navigation load rather than treating a grant as readiness', async () => {
  vi.mocked(HTMLFormElement.prototype.submit).mockImplementation(function (this: HTMLFormElement) {
    submissions.push({ action: this.action, target: this.target, body: '' })
  })
  const { container, findByRole, queryByText } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  expect((await findByRole('status')).textContent).toContain('正在加载新页面')
  const frame = container.querySelector('iframe')!
  await fireEvent.load(frame)
  expect(queryByText(/正在加载新页面/)).toBeTruthy()
  opaqueNavigation(frame)
  await waitFor(() => expect(queryByText(/正在加载新页面/)).toBeNull())
})

it('keeps the old frame during replacement, rejects failed navigation and ignores its late load', async () => {
  const { container, rerender, findByRole } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const oldFrame = container.querySelector('iframe')!
  vi.mocked(HTMLFormElement.prototype.submit).mockImplementation(function (this: HTMLFormElement) {
    submissions.push({ action: this.action, target: this.target, body: '' })
  })
  getPreview.mockResolvedValue(artifact('app', 'artifact-b'))
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(submissions).toHaveLength(2))
  const nextFrame = container.querySelector(`iframe[name="${submissions[1].target}"]`)!
  expect(container.querySelectorAll('iframe')).toHaveLength(2)
  await fireEvent.error(nextFrame)
  expect((await findByRole('alert')).textContent).toContain('当前仍显示上次打开的页面')
  expect(container.querySelector('iframe')).toBe(oldFrame)
  await fireEvent.load(nextFrame)
  expect(container.querySelector('iframe')).toBe(oldFrame)
})

it('preserves a named HTML browsing context on a known unchanged version', async () => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>same</p>', version: 'v1' })
  const { container, rerender } = mountFile('site/index.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(readPreviewFile).toHaveBeenCalledTimes(2))
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(requestPreviewSession).toHaveBeenCalledTimes(1)
  expect(submissions).toHaveLength(1)
  expect(container.querySelector('iframe')).toBe(frame)
})

it.each(['v2', null])('reloads named HTML when its version changes or is unknown (%s)', async (version) => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>first</p>', version: 'v1' })
  const { rerender } = mountFile('site/index.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>second</p>', version })
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(submissions).toHaveLength(2))
  expect(requestPreviewSession).toHaveBeenCalledTimes(2)
})

it('does not treat two unknown named HTML versions as unchanged', async () => {
  readPreviewFile.mockResolvedValue({ path: 'site/index.html', content: '<p>mutable</p>' })
  const { rerender } = mountFile('site/index.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(submissions).toHaveLength(2))
})

it('does not reuse a known version across different named files', async () => {
  readPreviewFile.mockResolvedValue({ path: 'a.html', content: '<p>A</p>', version: 'v1' })
  const { rerender } = mountFile('a.html')
  await waitFor(() => expect(submissions).toHaveLength(1))
  readPreviewFile.mockResolvedValue({ path: 'b.html', content: '<p>B</p>', version: 'v1' })
  await rerender({ path: 'b.html' })
  await waitFor(() => expect(submissions).toHaveLength(2))
  expect(new URLSearchParams(submissions[1].body).get('path')).toBe('/_cheese/room/b.html')
})

it('does not post an authorization that finishes while the pane is inactive', async () => {
  let finish: (value: { url: string; grant: string }) => void = () => {}
  requestPreviewSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { rerender } = mountFile('site/index.html')
  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalledTimes(1))
  await rerender({ active: false })
  finish({ url: `${url}_cheese/session`, grant: 'inactive-grant' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submissions).toHaveLength(0)
  await rerender({ active: true })
  await waitFor(() => expect(submissions).toHaveLength(1))
  expect(submissions[0].body).not.toContain('inactive-grant')
})

it('rejects a named file read that finishes after the path changes', async () => {
  let finish: (value: { path: string; content: string; version: string }) => void = () => {}
  readPreviewFile.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { rerender } = mountFile('a.html')
  await waitFor(() => expect(readPreviewFile).toHaveBeenCalledWith('topic-a', 'a.html'))
  readPreviewFile.mockResolvedValue({ path: 'b.html', content: '<p>B</p>', version: 'v2' })
  await rerender({ path: 'b.html' })
  await waitFor(() => expect(submissions).toHaveLength(1))
  finish({ path: 'a.html', content: '<p>A</p>', version: 'v1' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submissions).toHaveLength(1)
  expect(new URLSearchParams(submissions[0].body).get('path')).toBe('/_cheese/room/b.html')
})

it('ignores a grant from a prior topic without requiring remount', async () => {
  let finish: (value: { url: string; grant: string }) => void = () => {}
  requestPreviewSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { rerender } = mountFile('site/index.html')
  await waitFor(() =>
    expect(requestPreviewSession).toHaveBeenCalledWith('topic-a', {
      path: 'site/index.html',
      version: undefined,
    })
  )
  await rerender({ topicId: 'topic-b' })
  await waitFor(() => expect(submissions).toHaveLength(1))
  finish({ url: `${url}_cheese/session`, grant: 'stale-grant' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submissions).toHaveLength(1)
  expect(requestPreviewSession).toHaveBeenLastCalledWith('topic-b', {
    path: 'site/index.html',
    version: undefined,
  })
})

it.each([false, true])('refreshes changed static content and preserves the same version (large=%s)', async (large) => {
  getPreview.mockResolvedValue({ ...artifact('file'), version: 'content-a' })
  if (large) readPreviewFile.mockResolvedValue({ path: 'report.html', content: null, too_large: true, version: null })
  const { container, rerender, getByTitle } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')
  await rerender({ refreshTick: 1 })
  await waitFor(() => expect(readPreviewFile).toHaveBeenCalledTimes(2))
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submissions).toHaveLength(1)
  getPreview.mockResolvedValue({ ...artifact('file'), version: 'content-b' })
  await rerender({ refreshTick: 2 })
  await waitFor(() => expect(submissions).toHaveLength(2))
  expect(container.querySelector('iframe')).not.toBe(frame)
  await fireEvent.click(getByTitle('刷新'))
  await waitFor(() => expect(submissions).toHaveLength(3))
})
