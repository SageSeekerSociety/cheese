import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
vi.mock('../../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  readPreviewFile: (...args: unknown[]) => readPreviewFile(...args),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
}))

import PanelPreview from './PanelPreview.vue'

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
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
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
    expect(document.querySelector(`iframe[name="${this.target}"]`)).toBeTruthy()
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
  expect(document.querySelector('form')).toBeNull()
})

it('shows failed authorization without mounting an old platform URL', async () => {
  requestPreviewSession.mockRejectedValue(new Error('预览授权失败'))
  const { container, findByText } = mount()
  expect(await findByText(/预览授权失败/)).toBeTruthy()
  expect(container.querySelector('iframe')).toBeNull()
  expect(submissions).toHaveLength(0)
})

it('rejects a misconfigured same-origin authorization destination', async () => {
  requestPreviewSession.mockResolvedValue({ url: `${location.origin}/_cheese/session`, grant: 'preview-grant' })
  const { container, findByText } = mount()
  expect(await findByText(/预览地址未与平台隔离/)).toBeTruthy()
  expect(container.querySelector('iframe')).toBeNull()
  expect(submissions).toHaveLength(0)
})

it('ignores a late grant after switching topics', async () => {
  let finish: (value: { url: string; grant: string }) => void = () => {}
  requestPreviewSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { rerender, container } = mount()
  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalledWith('topic-a'))
  getPreview.mockResolvedValue(null)
  await rerender({ topicId: 'topic-b' })
  await waitFor(() => expect(getPreview).toHaveBeenCalledWith('topic-b'))
  finish({ url: `${url}_cheese/session`, grant: 'old-topic-grant' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(container.querySelector('iframe')).toBeNull()
  expect(submissions).toHaveLength(0)
})

it('ignores a late file read after switching topics', async () => {
  let finish: (value: { path: string; content: string }) => void = () => {}
  getPreview.mockResolvedValue(artifact('file'))
  readPreviewFile.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  const { rerender, container } = mount()
  await waitFor(() => expect(readPreviewFile).toHaveBeenCalled())
  getPreview.mockResolvedValue(null)
  await rerender({ topicId: 'topic-b' })
  finish({ path: 'old-secret.html', content: '<p>old topic</p>' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(container.textContent).not.toContain('old-secret')
  expect(container.querySelector('iframe')).toBeNull()
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
  expect(container.querySelector('iframe')).toBe(frame)
  getPreview.mockResolvedValue(artifact('file', 'artifact-b'))
  await rerender({ refreshTick: 2 })
  await waitFor(() => expect(submissions).toHaveLength(3))
  expect(container.querySelector('iframe')).toBe(frame)
})

it('opens the platform preview launch route in a new tab', async () => {
  const opened = vi.spyOn(window, 'open').mockReturnValue(null)
  const { getByTitle } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  await fireEvent.click(getByTitle('在新标签页打开'))
  expect(opened).toHaveBeenCalledWith('/previews/topic-a', '_blank', 'noopener')
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
  expect(container.querySelector('iframe')).toBe(frame)
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
  expect(container.querySelector('iframe')).toBe(frame)
  await fireEvent.click(getByTitle('刷新'))
  await waitFor(() => expect(submissions).toHaveLength(3))
})
