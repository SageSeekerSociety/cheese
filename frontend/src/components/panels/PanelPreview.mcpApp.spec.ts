// A third party's MCP View (`io.modelcontextprotocol/ui`): same preview panel and
// preview source as a room artifact, but a DIFFERENT sandbox. The room's own HTML
// is served with `allow-same-origin` (it comes from the content origin); a View we
// did not write must never get `allow-scripts` together with it.
import type { PreviewInfo } from '../../cx_types'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

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
import { createMcpAppBridge, MCP_APP_MIME, MCP_APP_SANDBOX } from '@/lib/mcpAppBridge'

const url = 'https://preview-topic-a-mcp.example/'
const artifact: PreviewInfo = {
  kind: 'mcp-app',
  path: 'ui://pkg/app.html',
  server: 'figma',
  mime: MCP_APP_MIME,
  url: 'https://preview-topic-a.example/',
  artifact_id: 'artifact-mcp',
}
// The frame's url is the authorized session url, not the artifact's advertised url.
const frameUrl = `${url}_cheese/session`
const submissions: { action: string; target: string }[] = []

function opaqueNavigation(frame: Element) {
  Object.defineProperty(frame, 'contentDocument', { configurable: true, get: () => null })
  frame.dispatchEvent(new Event('load'))
}

function mount(submitQuestion?: SubmitPreviewQuestion) {
  return render(PanelPreviewHost, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true, submitQuestion },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  submissions.length = 0
  getPreview.mockResolvedValue(artifact)
  requestPreviewSession.mockResolvedValue({ url: frameUrl, grant: 'preview-grant' })
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    const target = document.querySelector(`iframe[name="${this.target}"]`)!
    queueMicrotask(() => opaqueNavigation(target))
    submissions.push({ action: this.action, target: this.target })
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it('mounts a third-party View in a sandbox without allow-same-origin', async () => {
  const { container } = mount()
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe')!
  expect(frame.getAttribute('sandbox')).toBe(MCP_APP_SANDBOX)
  expect(frame.getAttribute('sandbox')).toContain('allow-scripts')
  expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin')
  // The bytes are the remote server's: nothing is read as a room file here, and
  // the grant carries only which artifact this is.
  expect(readPreviewFile).not.toHaveBeenCalled()
  expect(requestPreviewSession).toHaveBeenCalledWith('topic-a', { artifact_id: 'artifact-mcp' })
})

it('a ui/message from the View is submitted as the next room question', async () => {
  const submitQuestion = vi.fn().mockReturnValue(true)
  const { container } = mount(submitQuestion)
  await waitFor(() => expect(submissions).toHaveLength(1))
  const frame = container.querySelector('iframe') as HTMLIFrameElement
  // The bridge is built when the frame's load arrives with a browsing context; the
  // mocked form submit fires that load on a microtask, this restates it on purpose.
  opaqueNavigation(frame)
  window.dispatchEvent(
    new MessageEvent('message', {
      data: {
        jsonrpc: '2.0',
        id: 12,
        method: 'ui/message',
        params: { role: 'user', content: [{ type: 'text', text: '把这个数字改成红色' }] },
      },
      source: frame.contentWindow as unknown as MessageEventSource,
    })
  )
  await waitFor(() => expect(submitQuestion).toHaveBeenCalledTimes(1))
  const question = submitQuestion.mock.calls[0]![0]
  expect(question.content).toBe('把这个数字改成红色')
  // 应用没有文件身份，引用借「圈一块区域」那个形状，凭这一帧自己的地址定位。
  expect(question.quotedContext).toMatchObject({ kind: 'web-region', url: frameUrl })
})

it('a ui/message is joined from its text parts, trimmed, and handed to the bridge handler', () => {
  const frame = { postMessage: vi.fn() } as unknown as Window
  const onMessage = vi.fn()
  const bridge = createMcpAppBridge(frame, { name: 'cheese', version: '1' }, { onMessage })
  const handled = bridge.handle(
    new MessageEvent('message', {
      data: {
        jsonrpc: '2.0',
        id: 3,
        method: 'ui/message',
        params: {
          role: 'user',
          content: [
            { type: 'text', text: ' 你好 ' },
            { type: 'text', text: '世界 ' },
          ],
        },
      },
      source: frame as unknown as MessageEventSource,
    })
  )
  expect(handled).toBe(true)
  expect(onMessage).toHaveBeenCalledWith({ role: 'user', text: '你好 世界' })
  expect(frame.postMessage).toHaveBeenCalledWith({ jsonrpc: '2.0', id: 3, result: {} }, '*')
})

it('an unreadable ui/message is acked but never becomes a message', () => {
  const frame = { postMessage: vi.fn() } as unknown as Window
  const onMessage = vi.fn()
  const bridge = createMcpAppBridge(frame, { name: 'cheese', version: '1' }, { onMessage })
  bridge.handle(
    new MessageEvent('message', {
      data: { jsonrpc: '2.0', id: 4, method: 'ui/message', params: { content: 'not an array' } },
      source: frame as unknown as MessageEventSource,
    })
  )
  expect(onMessage).not.toHaveBeenCalled()
  expect(frame.postMessage).toHaveBeenCalledWith({ jsonrpc: '2.0', id: 4, result: {} }, '*')
})
