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

import { snapshotAge, usePanelPreviewAppSleep, wakeSteps } from './appWake'

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
  await vi.advanceTimersByTimeAsync(2_000)
  expect(sleep.wakeSteps.value!.map((s) => s.state)).toEqual(['done', 'done', 'now', 'todo'])
  expect(reload).not.toHaveBeenCalled()
  api.getPreviewWakeStatus.mockResolvedValue({ machine: 'ready', tunnel_up: true, agent_working: false, app_up: true })
  await vi.advanceTimersByTimeAsync(2_000)
  expect(sleep.wakeSteps.value!.every((s) => s.state === 'done')).toBe(true)
  expect(reload).toHaveBeenCalledTimes(1)
  // 新页面装上、应用不再睡：进度收起。
  state.previewTunnelUp.value = true
  state.previewAppUp.value = true
  await flush()
  expect(sleep.wakeSteps.value).toBeNull()
  scope.stop()
})

it('a wake that does not get the app up in time gives the button back with a sentence', async () => {
  api.wakePreviewApp.mockResolvedValue({ started: true })
  api.getPreviewWakeStatus.mockResolvedValue({ machine: null, tunnel_up: false, agent_working: false, app_up: false })
  const { state, sleep, scope } = setup()
  state.previewTunnelUp.value = false
  await sleep.wake()
  await vi.advanceTimersByTimeAsync(181_000)
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
