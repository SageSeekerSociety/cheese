/**
 * 首屏不等对象存储：打开现场时先读「数据库里已有的行」那一版（后端 `resident`），
 * 它可能少最新那一头的几条、并带 `deferred`。这一版马上画出来，随后**再读一次**
 * ——这一次不带 `resident`，也不带游标——把跳过的记录补齐。
 *
 * 为什么不能靠往上翻补：跳过的是**最新**那一头，往上翻只会走进更早的记录，永远
 * 够不着它。所以这里钉的就是「必须整页重读一次」，而且只重读一次。
 */
import type { Block } from '../cx_types'

import { effectScope, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getTranscript: vi.fn(), sessionRecordImageUrl: vi.fn() }))
vi.mock('../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('./useSessionInspector', () => ({ useSessionInspector: () => ({}) }))
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

import { storageRemove } from '../lib/browserStorage'
import { forgetWindows } from '../lib/siteWindow'

import { usePanelSite } from './usePanelSite'

beforeEach(() => {
  forgetWindows()
  vi.useFakeTimers()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
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
    meta: { record: { type: 'assistant', message: { content: [] } }, parts: [] },
  }
}

function page(
  rows: Block[],
  extra: { has_more?: boolean; oldest_id?: string | null; newest_id?: string | null; deferred?: boolean } = {}
) {
  return {
    data: rows,
    has_more: extra.has_more ?? false,
    oldest_id: extra.oldest_id ?? null,
    newest_id: extra.newest_id ?? null,
    deferred: extra.deferred,
  }
}

function pane() {
  const el = document.createElement('div')
  let top = 0
  Object.defineProperty(el, 'scrollHeight', { get: () => 2000 })
  Object.defineProperty(el, 'clientHeight', { get: () => 500 })
  Object.defineProperty(el, 'scrollTop', {
    get: () => top,
    set: (v: number) => (top = Math.max(0, Math.min(v, 1500))),
  })
  return el
}

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

async function open() {
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
  site.scrollRef.value = pane()
  await settle()
  return { site, scope }
}

/** Which kinds of read were asked for, in order. */
function reads(): Array<{ resident: boolean; after: unknown; before: unknown }> {
  return api.getTranscript.mock.calls.map((c) => {
    const o = (c[1] ?? {}) as Record<string, unknown>
    return { resident: o.resident === true, after: o.after, before: o.before }
  })
}

/** Answer the ways the panel actually reads: the fast newest page (resident),
 *  the prefetch of an older page (before), the incremental refresh (after), and
 *  the full newest page (neither cursor, not resident). */
function serve(opts: { deferred?: boolean; incremental?: Block[]; full: Block[] }) {
  api.getTranscript.mockImplementation((_tid: string, o: Record<string, unknown> = {}) => {
    if (o.resident === true) {
      return Promise.resolve(
        opts.deferred
          ? page([block('b', 2)], { deferred: true, has_more: true, oldest_id: 'old', newest_id: 'n.b' })
          : page(opts.full, { newest_id: 'n.b' })
      )
    }
    if (o.after !== undefined) return Promise.resolve(page(opts.incremental ?? []))
    if (o.before !== undefined) return Promise.resolve(page([]))
    return Promise.resolve(page(opts.full, { newest_id: 'n.b' }))
  })
}

describe('a newest page that skipped the bucket', () => {
  it('is painted at once, then re-read in full without the flag or a cursor', async () => {
    serve({ deferred: true, full: [block('a', 1), block('b', 2)] })
    const { site, scope } = await open()

    // 第一页是带着 `resident` 问的：少了它，就是又坐在对象存储上等。
    expect(reads()[0]!.resident).toBe(true)
    expect(site.transcript.value.map((b) => b.id)).toEqual(['b'])

    // 补齐的那一次重读：既不带 resident，也不带游标——它是把最新的一页整页重来，
    // 因为跳过的那几条在最新那一头，往上翻够不着。
    vi.advanceTimersByTime(3000)
    await settle()

    expect(reads().some((r) => !r.resident && r.after === undefined && r.before === undefined)).toBe(true)
    expect(site.transcript.value.map((b) => b.id)).toEqual(['a', 'b'])
    scope.stop()
  })

  it('does not re-read when the page was complete', async () => {
    serve({ deferred: false, full: [block('a', 1), block('b', 2)] })
    const { site, scope } = await open()
    vi.advanceTimersByTime(3000)
    await settle()

    // 完整的一页不该多付一次整页重读：之后的刷新照旧走增量（带 after）。
    const after = reads().slice(1)
    expect(after.length).toBeGreaterThan(0)
    expect(after.every((r) => r.after !== undefined || r.before !== undefined)).toBe(true)
    expect(site.transcript.value.map((b) => b.id)).toEqual(['a', 'b'])
    scope.stop()
  })
})
