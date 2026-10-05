/** 预览那一格的轮询：什么时候问、什么时候不问。
 *
 * 这一格看着的是「当前预览」——芝士随时可能换掉它。所以它在屏幕上的时候每 20 秒
 * 重问一次元数据（只问元数据：产物没变就不会重新提交一次授权，正在看的 iframe 也
 * 不会被拆掉重建，见 PanelPreview.session.spec.ts 里那几条）。
 *
 * 三条不问的理由各自是一条，而且都不是「省一点流量」：
 *   1. 不在屏幕上的那一格不问——没人在看，重取的结果只是放着；
 *   2. 页面在后台不问——后台标签的轮询是白花的，回到前台自然会重取；
 *   3. 指定了文件的那一格不问——它不跟着当前预览走，文件变了靠每一轮收工那一下。
 *
 * 定时器是这里唯一的时间来源，所以这个文件全程用假时钟：真等 20 秒等于把这份 spec
 * 变成 20 秒。假时钟下 waitFor 的轮询本身也停在原地，所以每一条都用微任务把
 * 组件的 Promise 链接干净，再断言调了几次。
 */
import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

const POLL_MS = 20_000

function artifact(id = 'artifact-a'): PreviewInfo {
  return {
    kind: 'file',
    path: 'report.html',
    mime: 'text/html',
    url: 'https://preview-topic-a.example/',
    artifact_id: id,
    tunnel_up: true,
  }
}

function mount(props: Record<string, unknown> = {}) {
  return render(PanelPreviewHost, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 把组件的 Promise 链走完。假时钟下 setTimeout 永远不响，所以只能走微任务。 */
async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve()
}

/** 时间往前走一格，再把这一步引起的请求走完。 */
async function tick(ms = POLL_MS) {
  vi.advanceTimersByTime(ms)
  await flush()
}

/** 把页面说成在后台，或者回到前台。`hidden` 在 happy-dom 里不是原型上的取数器，
 *  只能盖在这一个 document 上（这个文件里也没有第二个）。 */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    const frame = document.querySelector(`iframe[name="${this.target}"]`)!
    Object.defineProperty(frame, 'contentDocument', { configurable: true, get: () => null })
    queueMicrotask(() => frame.dispatchEvent(new Event('load')))
  })
  getPreview.mockResolvedValue(artifact())
  readPreviewFile.mockResolvedValue({
    path: 'report.html',
    content: '<h1>hi</h1>',
    version: 'v1',
    bytes: 11,
    binary: false,
    too_large: false,
  })
  requestPreviewSession.mockResolvedValue({
    url: 'https://preview-topic-a.example/_cheese/session',
    grant: 'preview-grant',
  })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  // 后台那一条改的是 document 自己，把这一层盖上去的东西拿掉。
  Reflect.deleteProperty(document, 'hidden')
})

describe('预览那一格的轮询', () => {
  it('在屏幕上的那一格每 20 秒重问一次当前预览是什么', async () => {
    mount()
    await flush()
    expect(getPreview).toHaveBeenCalledTimes(1)

    await tick()
    expect(getPreview).toHaveBeenCalledTimes(2)

    await tick()
    expect(getPreview).toHaveBeenCalledTimes(3)
  })

  it('不在屏幕上的那一格不问，回到屏幕上才开始问', async () => {
    const { rerender } = mount({ active: false })
    await flush()
    expect(getPreview).not.toHaveBeenCalled()

    await tick(3 * POLL_MS)
    expect(getPreview).not.toHaveBeenCalled()

    // 切到这一格 = 开抽屉：立刻读一次，往后每 20 秒一次。
    await rerender({ active: true })
    await flush()
    expect(getPreview).toHaveBeenCalledTimes(1)
    await tick()
    expect(getPreview).toHaveBeenCalledTimes(2)
  })

  it('页面在后台的时候不问，回到前台接着问', async () => {
    mount()
    await flush()
    expect(getPreview).toHaveBeenCalledTimes(1)

    setHidden(true)
    await tick()
    expect(getPreview).toHaveBeenCalledTimes(1)

    setHidden(false)
    await tick()
    expect(getPreview).toHaveBeenCalledTimes(2)
  })

  it('指定了文件的那一格不轮询——它不跟着当前预览走', async () => {
    mount({ path: 'site/index.html' })
    await flush()
    expect(readPreviewFile).toHaveBeenCalledTimes(1)

    await tick(3 * POLL_MS)
    expect(readPreviewFile).toHaveBeenCalledTimes(1)
    expect(getPreview).not.toHaveBeenCalled()
  })

  it('离开这一格就停下来，卸掉之后也不再有请求', async () => {
    const { rerender, unmount } = mount()
    await flush()
    expect(getPreview).toHaveBeenCalledTimes(1)

    await rerender({ active: false })
    await tick(3 * POLL_MS)
    expect(getPreview).toHaveBeenCalledTimes(1)

    unmount()
    await tick(3 * POLL_MS)
    expect(getPreview).toHaveBeenCalledTimes(1)
  })
})
