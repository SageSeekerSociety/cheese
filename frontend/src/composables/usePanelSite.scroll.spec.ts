/**
 * 「现场」那一格停在哪：打开停在最新的一条；读者往上翻之后，3 秒一次的刷新和
 * socket 上来的一行都不能把他拽回底部；他自己滚回底部之后，新的行接着跟。
 *
 * jsdom 不做布局，所以这里用一个会记账的假滚动容器代替 `.panel-site`：scrollHeight /
 * clientHeight 由测试按盒子给，scrollTop 的赋值照浏览器那样夹在可滚范围内。
 */
import type { Block } from '../cx_types'

import { effectScope, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getTranscript: vi.fn(), sessionRecordImageUrl: vi.fn() }))
vi.mock('../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('./useSessionInspector', () => ({ useSessionInspector: () => ({}) }))
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

import { storageRemove } from '../lib/browserStorage'

import { usePanelSite } from './usePanelSite'

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
  // 钉底排的是下一个动画帧：测试里当场执行，和这一拍落在一起。
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0)
    return 0
  })
  storageRemove('cheese.site.view')
  api.getTranscript.mockReset()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function pane(box: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  const el = document.createElement('div')
  Object.defineProperty(el, 'scrollHeight', { get: () => box.scrollHeight })
  Object.defineProperty(el, 'clientHeight', { get: () => box.clientHeight })
  Object.defineProperty(el, 'scrollTop', {
    get: () => box.scrollTop,
    set: (v: number) => (box.scrollTop = Math.max(0, Math.min(v, box.scrollHeight - box.clientHeight))),
  })
  return el
}

function block(id: string, second: number): Block {
  return {
    id,
    conversation_id: 'room',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-a',
    content: '',
    created_at: `2026-10-19T10:00:${String(second).padStart(2, '0')}Z`,
    refs: [],
    meta: { record: { type: 'assistant', message: { content: [] } }, parts: [], tool: 'Bash' },
  }
}

function row(id: string, second: number): Block {
  return { ...block(id, second), run_record: false } as Block
}

function page(rows: Block[]) {
  return { data: rows, has_more: false }
}

const OPEN = page([block('a', 1), block('b', 2)])

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

async function openAtBottom() {
  const box = { scrollHeight: 2000, clientHeight: 500, scrollTop: 0 }
  const el = pane(box)
  const scope = effectScope()
  const site = scope.run(() =>
    usePanelSite({
      topicId: () => 'room',
      active: () => true,
      refreshTick: () => 0,
      runningTurns: () => ({}),
      agentControl: () => null,
    })
  )!
  site.scrollRef.value = el
  await settle()
  return { box, el, site, scope }
}

describe('where 现场 opens', () => {
  it('lands on the newest record', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, scope } = await openAtBottom()
    expect(box.scrollTop).toBe(1500)
    scope.stop()
  })

  it('lands on the newest record of the teammate the reader switches to', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, site, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))

    site.selectAgent('cheese-a')
    await settle()

    expect(box.scrollTop).toBe(1500)
    scope.stop()
  })
})

describe('a reader who scrolled up', () => {
  it('is not moved by the 3-second refresh', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))

    api.getTranscript.mockClear()
    vi.advanceTimersByTime(3000)
    await settle()

    expect(api.getTranscript).toHaveBeenCalledTimes(1)
    expect(box.scrollTop).toBe(600)
    scope.stop()
  })

  it('is not moved by a refresh that was already in flight when they scrolled up', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, scope } = await openAtBottom()

    // The 3-second refresh starts while the reader is still at the end, then they
    // scroll up before the page comes back.
    let answer!: (value: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    vi.advanceTimersByTime(3000)
    await Promise.resolve()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    answer(OPEN)
    await settle()

    expect(box.scrollTop).toBe(600)
    scope.stop()
  })

  it('is not moved by a record arriving over the socket', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, site, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))

    box.scrollHeight = 2400
    site.receive(row('c', 3))
    await settle()

    expect(box.scrollTop).toBe(600)
    scope.stop()
  })
})

describe('a reader back at the end', () => {
  it('follows a record arriving over the socket again', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { box, el, site, scope } = await openAtBottom()

    box.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    box.scrollTop = 1500
    el.dispatchEvent(new Event('scroll'))

    box.scrollHeight = 2400
    site.receive(row('c', 3))
    await settle()

    expect(box.scrollTop).toBe(1900)
    scope.stop()
  })
})

describe('a read that is slow or fails', () => {
  it('keeps one read in flight and runs one more after it', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { scope } = await openAtBottom()

    // The backend is slow: the read has not come back when two more refreshes
    // and a socket line ask for one.
    api.getTranscript.mockClear()
    let answer!: (value: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    vi.advanceTimersByTime(3000)
    await settle()
    expect(api.getTranscript).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(3000)
    vi.advanceTimersByTime(3000)
    await settle()
    expect(api.getTranscript).toHaveBeenCalledTimes(1)

    // It comes back: the waiting refreshes collapse into one more read, not three.
    answer(OPEN)
    await settle()
    expect(api.getTranscript).toHaveBeenCalledTimes(2)

    scope.stop()
  })

  it('keeps the records on screen when a background refresh fails', async () => {
    api.getTranscript.mockResolvedValue(OPEN)
    const { site, scope } = await openAtBottom()
    const rows = site.transcript.value.length

    api.getTranscript.mockRejectedValue(new Error('请求等待超时，请重试'))
    vi.advanceTimersByTime(3000)
    await settle()

    expect(site.errorMsg.value).toBeNull()
    expect(site.transcript.value).toHaveLength(rows)
    scope.stop()
  })

  it('shows the error when there is nothing on screen yet', async () => {
    api.getTranscript.mockRejectedValue(new Error('请求等待超时，请重试'))
    const { site, scope } = await openAtBottom()

    expect(site.errorMsg.value).toBe('请求等待超时，请重试')
    scope.stop()
  })
})

describe('a topic with no records yet', () => {
  it('does not flash the skeleton on every background refresh', async () => {
    api.getTranscript.mockResolvedValue(page([]))
    const { site, scope } = await openAtBottom()
    expect(site.transcript.value).toHaveLength(0)
    expect(site.loading.value).toBe(false)

    // 下一次刷新还在路上：屏上该一直停在那句空状态里，不该切回骨架屏。
    let answer!: (value: ReturnType<typeof page>) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    vi.advanceTimersByTime(3000)
    await settle()
    expect(site.loading.value).toBe(false)

    answer(page([]))
    await settle()
    expect(site.loading.value).toBe(false)
    scope.stop()
  })
})
