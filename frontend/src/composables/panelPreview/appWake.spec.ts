import type { PreviewFrame } from '../usePreviewFrames'

import { effectScope, nextTick, reactive } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { createPreviewState } from './previewState'

const api = vi.hoisted(() => ({
  getPreviewSnapshot: vi.fn(),
  putPreviewSnapshot: vi.fn(),
  wakePreviewApp: vi.fn(),
  getPreviewWakeStatus: vi.fn(),
}))
vi.mock('../../api', () => api)

import { snapshotAge, usePanelPreviewAppSleep, wakePollDelay, wakeSteps } from './appWake'

import { setLocale } from '@/i18n'

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  await nextTick()
}

const liveFrame = { id: 1, live: true } as PreviewFrame

function setup() {
  const props = reactive({ topicId: 'task-1' as string | null, path: null as string | null, agentName: '小 C' })
  const state = createPreviewState()
  state.previewAppNote.value = 'npm run dev'
  state.previewArtifactId.value = 'art-1'
  state.previewTunnelUp.value = true
  state.previewAppUp.value = true
  const reload = vi.fn(async () => {})
  const scope = effectScope()
  const sleep = scope.run(() =>
    usePanelPreviewAppSleep({ props: props as never, state, displayed: () => liveFrame, reload })
  )!
  return { props, state, reload, scope, sleep }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  api.getPreviewSnapshot.mockResolvedValue(null)
  api.putPreviewSnapshot.mockResolvedValue(null)
})
afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

it('the four steps tick in order: done, the first not done spins, the rest wait', () => {
  expect(wakeSteps({ machine: true, tunnel: false, agent: false, port: false }).map((s) => s.state)).toEqual([
    'done',
    'now',
    'todo',
    'todo',
  ])
  expect(wakeSteps({ machine: true, tunnel: true, agent: true, port: true }).every((s) => s.state === 'done')).toBe(
    true
  )
})

it('a snapshot age reads as minutes, hours or days ago', () => {
  const now = 1_000_000_000_000
  expect(snapshotAge(now / 1000 - 10, now)).toBe('刚刚')
  expect(snapshotAge(now / 1000 - 4 * 60, now)).toBe('4 分钟前')
  expect(snapshotAge(now / 1000 - 3 * 3600, now)).toBe('3 小时前')
  expect(snapshotAge(now / 1000 - 2 * 86400, now)).toBe('2 天前')
})

it('a snapshot the frame took is kept on the platform and shown when the app sleeps', async () => {
  const { state, sleep, scope } = setup()
  sleep.keep(liveFrame, { html: '<p>hi</p>', href: 'https://p/signup', title: 't', viewport: { w: 1, h: 1 } })
  expect(api.putPreviewSnapshot).toHaveBeenCalledWith('task-1', 'art-1', {
    html: '<p>hi</p>',
    href: 'https://p/signup',
    title: 't',
  })
  state.previewTunnelUp.value = false
  await flush()
  // 手上已经有这一件的快照：不再去平台上取。
  expect(api.getPreviewSnapshot).not.toHaveBeenCalled()
  expect(sleep.appSnapshot.value?.html).toBe('<p>hi</p>')
  scope.stop()
})

it('a sleeping app with no snapshot in hand reads the kept one once', async () => {
  api.getPreviewSnapshot.mockResolvedValue({
    html: '<p>kept</p>',
    href: '',
    title: '',
    taken_at: Date.now() / 1000 - 240,
  })
  const { state, sleep, scope } = setup()
  state.previewAppUp.value = false
  await flush()
  expect(api.getPreviewSnapshot).toHaveBeenCalledTimes(1)
  expect(sleep.appSnapshot.value).toEqual({ html: '<p>kept</p>', age: '4 分钟前' })
  scope.stop()
})

it('waking polls the steps and reloads the preview once the app answers', async () => {
  api.wakePreviewApp.mockResolvedValue({ started: true })
  const { state, sleep, reload, scope } = setup()
  state.previewTunnelUp.value = false
  state.previewAppUp.value = false
  await flush()
  await sleep.wake()
  expect(sleep.wakeSteps.value!.map((s) => s.state)).toEqual(['now', 'todo', 'todo', 'todo'])
  api.getPreviewWakeStatus.mockResolvedValue({ machine: 'ready', tunnel_up: true, agent_working: true, app_up: false })
  // 轮询间隔是逐格放松的（见 `wakePollDelay`）：按表上的下一格推进，别写死 2 秒。
  await vi.advanceTimersByTimeAsync(wakePollDelay(1))
  expect(sleep.wakeSteps.value!.map((s) => s.state)).toEqual(['done', 'done', 'now', 'todo'])
  expect(reload).not.toHaveBeenCalled()
  api.getPreviewWakeStatus.mockResolvedValue({ machine: 'ready', tunnel_up: true, agent_working: false, app_up: true })
  await vi.advanceTimersByTimeAsync(wakePollDelay(2))
  // 应用答了：重新载入一次，然后卡收起。收在载入之后、不管载入成不成——留在「四步
  // 全绿」上会把唤醒按钮一直挡住，读者只能重挂面板。
  expect(reload).toHaveBeenCalledTimes(1)
  expect(sleep.wakeSteps.value).toBeNull()
  scope.stop()
})

it('a reload that throws after the app answers still gives the card back', async () => {
  api.wakePreviewApp.mockResolvedValue({ started: true })
  api.getPreviewWakeStatus.mockResolvedValue({ machine: 'ready', tunnel_up: true, agent_working: false, app_up: true })
  const { state, sleep, reload, scope } = setup()
  state.previewTunnelUp.value = false
  state.previewAppUp.value = false
  reload.mockRejectedValueOnce(new Error('读预览失败'))
  await sleep.wake()
  await vi.advanceTimersByTimeAsync(wakePollDelay(1))
  expect(reload).toHaveBeenCalledTimes(1)
  expect(sleep.wakeSteps.value).toBeNull()
  // 还能再按一次：进度清掉了，判据不再挡。
  api.wakePreviewApp.mockResolvedValue({ started: true })
  await sleep.wake()
  expect(api.wakePreviewApp).toHaveBeenCalledTimes(2)
  scope.stop()
})

it('a snapshot the previous artifact took is not stored under the new one', async () => {
  // 换件时「现在这一件」的指针先动、新文档才在路上，旧帧仍会在台前拍一张。照收就会
  // 署成新那一件——它睡着时显示的是上一件的页面。
  const { state, sleep, scope } = setup()
  state.previewArtifactId.value = 'art-2'
  const stale = { id: 1, live: true, identity: '["t","app","art-1","u",null,null]' } as PreviewFrame
  sleep.keep(stale, { html: '<p>old</p>', href: 'u', title: 't', viewport: { w: 1, h: 1 } })
  expect(api.putPreviewSnapshot).not.toHaveBeenCalled()
  expect(sleep.appSnapshot.value).toBeNull()
  // 这一件自己拍的照收。
  sleep.keep({ id: 2, live: true, identity: '["t","app","art-2","u",null,null]' } as PreviewFrame, {
    html: '<p>new</p>',
    href: 'u',
    title: 't',
    viewport: { w: 1, h: 1 },
  })
  expect(api.putPreviewSnapshot).toHaveBeenCalledWith(
    'task-1',
    'art-2',
    expect.objectContaining({ html: '<p>new</p>' })
  )
  scope.stop()
})

it('waking again while the reload is still running does not start a second one', async () => {
  api.wakePreviewApp.mockResolvedValue({ started: true })
  api.getPreviewWakeStatus.mockResolvedValue({ machine: 'ready', tunnel_up: true, agent_working: false, app_up: true })
  let release: () => void = () => {}
  const { state, sleep, reload, scope } = setup()
  reload.mockImplementationOnce(() => new Promise<void>((ok) => (release = ok)))
  state.previewTunnelUp.value = false
  state.previewAppUp.value = false
  await sleep.wake()
  await vi.advanceTimersByTimeAsync(2_000)
  // 进度收了、卡上又出现「唤醒」按钮，但载入还没走完：这时按下去不该再起一轮。
  expect(sleep.wakeSteps.value).toBeNull()
  await sleep.wake()
  expect(api.wakePreviewApp).toHaveBeenCalledTimes(1)
  release()
  await flush()
  scope.stop()
})

it('a snapshot read that fails is retried the next time the app sleeps', async () => {
  api.getPreviewSnapshot.mockRejectedValueOnce(new Error('网络抖了一下'))
  const { state, sleep, scope } = setup()
  state.previewAppUp.value = false
  await flush()
  expect(api.getPreviewSnapshot).toHaveBeenCalledTimes(1)
  expect(sleep.appSnapshot.value).toBeNull()
  // 再睡一次（预览信息轮询会这样翻）：这一件没取成过，重试。
  state.previewAppUp.value = true
  await flush()
  api.getPreviewSnapshot.mockResolvedValue({
    html: '<p>kept</p>',
    href: '',
    title: '',
    taken_at: Date.now() / 1000,
  })
  state.previewAppUp.value = false
  await flush()
  expect(api.getPreviewSnapshot).toHaveBeenCalledTimes(2)
  expect(sleep.appSnapshot.value?.html).toBe('<p>kept</p>')
  scope.stop()
})

it('a snapshot read that fails after the artifact changed does not re-ask the new one', async () => {
  let failFirst: (e: Error) => void = () => {}
  api.getPreviewSnapshot.mockImplementationOnce(
    () =>
      new Promise((_ok, no) => {
        failFirst = no
      })
  )
  const { state, scope } = setup()
  state.previewAppUp.value = false
  await flush()
  expect(api.getPreviewSnapshot).toHaveBeenCalledTimes(1)
  // 第一次还在路上时换了产物：新的这一件自己去取一次。
  api.getPreviewSnapshot.mockResolvedValue(null)
  state.previewArtifactId.value = 'art-2'
  await flush()
  expect(api.getPreviewSnapshot).toHaveBeenCalledTimes(2)
  // 换产物之前那一次的失败回来了。它不该把新产物的记号一起抹掉，否则再睡着一次又白问。
  failFirst(new Error('网络抖了一下'))
  await flush()
  state.previewAppUp.value = true
  await flush()
  state.previewAppUp.value = false
  await flush()
  expect(api.getPreviewSnapshot).toHaveBeenCalledTimes(2)
  scope.stop()
})

it('a wake that does not get the app up in time gives the button back with a sentence', async () => {
  api.wakePreviewApp.mockResolvedValue({ started: true })
  api.getPreviewWakeStatus.mockResolvedValue({ machine: null, tunnel_up: false, agent_working: false, app_up: false })
  const { state, sleep, scope } = setup()
  state.previewTunnelUp.value = false
  await sleep.wake()
  // 预算 180 秒；轮询间隔越往后越大，多给一格，别让最后那一问卡在窗口外。
  await vi.advanceTimersByTimeAsync(181_000 + wakePollDelay(99))
  expect(sleep.wakeSteps.value).toBeNull()
  expect(sleep.wakeError.value).toContain('180 秒')
  expect(sleep.wakeError.value).toContain('小 C')
  scope.stop()
})

it('a refused wake says why and keeps the button', async () => {
  api.wakePreviewApp.mockRejectedValue(new Error('没有权限'))
  const { sleep, scope } = setup()
  await sleep.wake()
  expect(sleep.wakeSteps.value).toBeNull()
  expect(sleep.wakeError.value).toContain('没有权限')
  scope.stop()
})

it('轮询节奏逐格放松：头两问密，之后松开但封顶', () => {
  expect(wakePollDelay(0)).toBe(2_000)
  expect(wakePollDelay(1)).toBe(2_000)
  expect(wakePollDelay(2)).toBe(3_000)
  expect(wakePollDelay(3)).toBe(5_000)
  expect(wakePollDelay(4)).toBe(8_000)
  // 一直用最后一档，不会停问。
  expect(wakePollDelay(50)).toBe(8_000)
  expect(wakePollDelay(-1)).toBe(2_000)
})
