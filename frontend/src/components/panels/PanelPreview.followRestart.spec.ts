/** 预览那一格自动跟住重启后的应用：实例换了、隧道断了又回来，都不该让人手动刷新。
 *
 * 后端把授权绑在「监听实例」上（#2349）：同一端口上一个新的开发服务器起来，实例指纹
 * 就变了，已经打开的那一帧发出去的请求会被全部拒掉（内容域回 404），页面白掉。所以判据
 * 在这里——面板自己要发现这次变化、拿新实例换一次授权、并且换得够快。文件预览不在
 * 这条路上。
 *
 * 定时器是这一格唯一的时间来源，所以用假时钟：真等 20 秒等于把这份 spec 变成 20 秒。
 */
import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
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

import PanelPreview from './PanelPreview.vue'

const url = 'https://preview-topic-a.example/'
// 实例指纹是 sha256(...) 的十六进制串；两个不同的值就代表两个不同的监听进程。
const instanceA = 'a'.repeat(64)
const instanceB = 'b'.repeat(64)
const POLL_MS = 20_000
const FAST_MS = 2_000

function appArtifact(instance: string, online = true): PreviewInfo {
  return {
    kind: 'app',
    path: 'Dev server',
    mime: 'text/html',
    url: online ? url : null,
    artifact_id: 'artifact-a',
    tunnel_up: online,
    instance,
  }
}

let submissions: { action: string; target: string; body: string }[]
function mount() {
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 把组件的 Promise 链走完。假时钟下 setTimeout 不响，只能走微任务。 */
async function flush() {
  for (let i = 0; i < 15; i += 1) await Promise.resolve()
}
/** 时间往前走一格，再把这一步引起的请求走完。 */
async function tick(ms: number) {
  vi.advanceTimersByTime(ms)
  await flush()
}
/** 让授权表投出去的那一帧走完导航：浏览器 loaded 事件由这一下补上。 */
async function loadFrame(target: string) {
  const frame = document.querySelector(`iframe[name="${target}"]`)!
  Object.defineProperty(frame, 'contentDocument', { configurable: true, get: () => null })
  frame.dispatchEvent(new Event('load'))
  await flush()
}
/** 让某一帧报加载失败：那一程导航判失败，屏幕上留下旧帧等下一轮。 */
async function failFrame(target: string) {
  const frame = document.querySelector(`iframe[name="${target}"]`)!
  frame.dispatchEvent(new Event('error'))
  await flush()
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  submissions = []
  getPreview.mockResolvedValue(appArtifact(instanceA))
  readPreviewFile.mockResolvedValue({ path: 'report.html', content: '<h1>hi</h1>', version: 'v1' })
  // 签到下来的授权绑在请求里那个实例上——和真后端一样，新实例签出来的是另一张。
  requestPreviewSession.mockImplementation((_topic: string, selection?: { instance?: string }) =>
    Promise.resolve({
      url: `${url}_cheese/session`,
      grant: `grant-${selection?.instance?.[0] ?? 'none'}`,
      resource: { kind: 'app', path: 'Dev server', instance: selection?.instance },
    })
  )
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    const target = document.querySelector(`iframe[name="${this.target}"]`)
    expect(target).toBeTruthy()
    submissions.push({
      action: this.action,
      target: this.target,
      body: new URLSearchParams(new FormData(this) as never).toString(),
    })
  })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('应用重启/断线后面板自己跟上去', () => {
  it('实例换了：拿新实例换一次授权，旧那一帧一直显示到新的装上', async () => {
    const { container, findByText, queryByText } = mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    expect(container.textContent).toContain('Dev server')
    expect(requestPreviewSession).toHaveBeenCalledTimes(1)

    // 开发服务器在同一个端口上重启：实例从 A 变成 B。
    getPreview.mockResolvedValue(appArtifact(instanceB))
    await tick(POLL_MS)

    // 新的授权绑的是新实例，投在新的一帧上——旧那一张授权（旧帧）没有被重新利用。
    expect(requestPreviewSession).toHaveBeenCalledTimes(2)
    expect(requestPreviewSession).toHaveBeenLastCalledWith('topic-a', {
      artifact_id: 'artifact-a',
      instance: instanceB,
    })
    expect(submissions).toHaveLength(2)
    expect(submissions[1]!.target).not.toBe(submissions[0]!.target)
    expect(submissions[1]!.body).not.toBe(submissions[0]!.body)
    // 双缓冲：旧帧还在，直到新帧装好。
    expect(container.querySelectorAll('iframe')).toHaveLength(2)

    // 那一闪有一句话解释。
    expect(await findByText('应用已重启，已自动重新载入')).toBeTruthy()

    // 新帧装好之后，那句解释让位给页面本身。
    await loadFrame(submissions[1]!.target)
    expect(queryByText('应用已重启，已自动重新载入')).toBeNull()
  })

  it('同一实例断线又回来：不重载也不提示（应用没死，页面自己会接上）', async () => {
    const { container } = mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    const firstFrame = container.querySelector('iframe')

    // 隧道断了：还是那个实例，但应用不在线。
    getPreview.mockResolvedValue(appArtifact(instanceA, false))
    await tick(POLL_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(1)
    expect(container.querySelector('iframe')).toBe(firstFrame)

    // 隧道回来了，还是同一实例——应用没死，不重新授权（重载会抹掉表单和 SPA 状态）。
    getPreview.mockResolvedValue(appArtifact(instanceA))
    await tick(FAST_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(1)
    expect(submissions).toHaveLength(1)
    expect(container.querySelector('iframe')).toBe(firstFrame)
    expect(container.textContent).not.toContain('应用已重启')
  })

  it('实例指纹缺失（探针没答）：不当成换实例，不重新授权也不提示', async () => {
    const { container, queryByText } = mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    expect(requestPreviewSession).toHaveBeenCalledTimes(1)

    // 后端隧道在线、但实例探针没答：url 有、instance 为 null。这不是「换了一个实例」——
    // 认错会签出一张没绑资源的空头授权。
    getPreview.mockResolvedValue({ ...appArtifact(instanceA), instance: null })
    await tick(POLL_MS)

    expect(requestPreviewSession).toHaveBeenCalledTimes(1)
    expect(submissions).toHaveLength(1)
    expect(queryByText('应用已重启，已自动重新载入')).toBeNull()
    // 状态仍是「在线」，不是「原实例已替换」。
    expect(container.textContent).not.toContain('原实例已替换')
  })

  it('第一次跟不上（新帧报错）：后面的轮询再试，成了才停', async () => {
    const { queryByText } = mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    expect(requestPreviewSession).toHaveBeenCalledTimes(1)

    // 实例换了：第一次跟过去，拿的是新实例。
    getPreview.mockResolvedValue(appArtifact(instanceB))
    await tick(POLL_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(2)
    expect(requestPreviewSession).toHaveBeenLastCalledWith('topic-a', {
      artifact_id: 'artifact-a',
      instance: instanceB,
    })

    // 这一程没成：新那一帧报加载失败。屏幕上仍是旧帧，实例也还是旧的。
    await failFrame(submissions[1]!.target)
    expect(submissions).toHaveLength(2)

    // 替换状态按 2 秒问：下一轮再跟一次，这次跟上。
    await tick(FAST_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(3)
    expect(requestPreviewSession).toHaveBeenLastCalledWith('topic-a', {
      artifact_id: 'artifact-a',
      instance: instanceB,
    })
    expect(submissions).toHaveLength(3)
    expect(submissions[2]!.target).not.toBe(submissions[0]!.target)

    // 新帧装好，那一句解释让位。
    await loadFrame(submissions[2]!.target)
    expect(queryByText('应用已重启，已自动重新载入')).toBeNull()
  })

  it('跟不过一分钟就交给手动刷新：点刷新还能重来一次', async () => {
    const { container, findByText, getByText, queryByText } = mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    expect(requestPreviewSession).toHaveBeenCalledTimes(1)

    // 实例换了，可每一次跟过去都失败：新那一帧报错，屏幕上一直是旧帧。
    getPreview.mockResolvedValue(appArtifact(instanceB))
    await tick(POLL_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(2)
    const failPending = async () => {
      const last = submissions[submissions.length - 1]
      if (last && document.querySelector(`iframe[name="${last.target}"]`)) await failFrame(last.target)
    }
    await failPending()
    // 一直失败到窗口过期（60 秒之后那几轮退避）。
    for (let i = 0; i < 8; i += 1) {
      await vi.advanceTimersByTimeAsync(POLL_MS)
      await flush()
      await failPending()
    }

    // 认输了：给出「刷新以打开当前实例」，而且不再自相矛盾地说一句「应用不可用」。
    expect(await findByText('原实例已被替换，新的还没就绪。刷新以打开当前实例')).toBeTruthy()
    expect(container.textContent).not.toContain('预览连接正常，但应用没有响应')
    const before = requestPreviewSession.mock.calls.length

    // 点「重试新内容」：手动刷新要能重来一次，不被那句「认输」堵死。
    await fireEvent.click(getByText('重试新内容'))
    await flush()
    expect(requestPreviewSession.mock.calls.length).toBe(before + 1)
    expect(requestPreviewSession).toHaveBeenLastCalledWith('topic-a', {
      artifact_id: 'artifact-a',
      instance: instanceB,
    })

    // 这一帧装好了，提示让位。
    await loadFrame(submissions[submissions.length - 1]!.target)
    expect(queryByText('原实例已被替换，新的还没就绪。刷新以打开当前实例')).toBeNull()
  })

  it('屏幕上那一帧还好好的：跟到新实例失败，也不该在它断线回来时被连坐重载', async () => {
    const { container } = mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    const firstFrame = container.querySelector('iframe')

    // 跟到 B 的这一程失败——但屏幕上一直是健康的 A，A 自己没坏。
    getPreview.mockResolvedValue(appArtifact(instanceB))
    await tick(POLL_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(2)
    await failFrame(submissions[1]!.target)

    // A 断线又回来：A 自己的导航没失败，不该重载它。退避是活的（这一程已经快问过
    // 一格），所以两次落到 2 秒、4 秒那两格上。
    getPreview.mockResolvedValue(appArtifact(instanceA, false))
    await tick(FAST_MS)
    getPreview.mockResolvedValue(appArtifact(instanceA))
    await tick(2 * FAST_MS)
    expect(requestPreviewSession).toHaveBeenCalledTimes(2)
    expect(submissions).toHaveLength(2)
    expect(container.querySelector('iframe')).toBe(firstFrame)
  })

  it('不在线时按 2 秒轮询，回到在线后退回 20 秒', async () => {
    mount()
    await flush()
    await loadFrame(submissions[0]!.target)
    expect(getPreview).toHaveBeenCalledTimes(1)

    // 掉线：第一次仍按 20 秒问到，之后间隔缩短。
    getPreview.mockResolvedValue(appArtifact(instanceA, false))
    await tick(POLL_MS)
    expect(getPreview).toHaveBeenCalledTimes(2)
    // 2 秒就问了一次——远快于正常档。
    await tick(FAST_MS)
    expect(getPreview).toHaveBeenCalledTimes(3)
    // 退避：下一次是 4 秒后，2 秒还没到。
    await tick(FAST_MS)
    expect(getPreview).toHaveBeenCalledTimes(3)

    // 回来了：间隔退回正常档。
    getPreview.mockResolvedValue(appArtifact(instanceA))
    await tick(2 * FAST_MS)
    expect(getPreview).toHaveBeenCalledTimes(4)
    // 正常档下 2 秒内不再问。
    await tick(FAST_MS)
    expect(getPreview).toHaveBeenCalledTimes(4)
    await tick(POLL_MS - FAST_MS)
    expect(getPreview).toHaveBeenCalledTimes(5)
  })
})
