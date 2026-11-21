/**
 * 往后读的增量页和 socket 先送来的行合在一起时，现场仍按时间排、同一 id 只留一条。
 *
 * 复现的是 bug review 报告 12 的那条路：打开时读回空页（带游标 0），随后 socket 先
 * 送来一条较新的 t2；下一次往后读回 [t1, t2]。从前增量页的新行一律拼在末尾，现场
 * 就成了 [t2, t1]，而且没有任何报错。
 */
import type { Block } from '../cx_types'

import { effectScope, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getTranscript: vi.fn() }))
vi.mock('../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

import { forgetWindows } from '../lib/siteWindow'

import { useSiteTranscript } from './useSiteTranscript'

beforeEach(() => {
  forgetWindows()
  api.getTranscript.mockReset()
})

function record(id: string, second: number, cursor?: string): Block {
  return {
    id,
    conversation_id: 'room',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-a',
    content: '',
    created_at: `2026-11-07T10:00:${String(second).padStart(2, '0')}Z`,
    refs: [],
    run_record: { type: 'assistant' },
    meta: cursor === undefined ? {} : { cursor },
  } as unknown as Block
}

function page(rows: Block[], newest: string | null) {
  return { data: rows, has_more: false, oldest_id: null, newest_id: newest }
}

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function mount() {
  const scope = effectScope()
  const site = scope.run(() =>
    useSiteTranscript({
      topicId: () => 'room',
      active: () => false,
      viewing: () => null,
      scrollRef: ref(null),
      following: ref(true),
      scrollToTail: () => {},
      captureAnchor: () => ({ id: null, offset: 0 }) as never,
      restoreAnchor: () => {},
      noteAgents: () => {},
      noteStarts: () => {},
    })
  )!
  return { site, scope }
}

const ids = (rows: Block[]) => rows.map((b) => b.id)

describe('useSiteTranscript: incremental page merged with socket rows', () => {
  it('keeps time order when the forward page brings an older row than one the socket already delivered', async () => {
    const t1 = record('t1', 1, '1')
    const t2 = record('t2', 2, '2')
    api.getTranscript.mockResolvedValueOnce(page([], '0')).mockResolvedValueOnce(page([t1, t2], '2'))
    const { site, scope } = mount()

    await site.load()
    await settle()
    expect(ids(site.transcript.value)).toEqual([])

    site.receive(record('t2', 2))
    expect(ids(site.transcript.value)).toEqual(['t2'])

    await site.load()
    await settle()
    // 第二次是往后读（after 游标 0），不是整页重读。
    expect(api.getTranscript.mock.calls[1][1]).toMatchObject({ after: '0' })
    expect(ids(site.transcript.value)).toEqual(['t1', 't2'])
    expect(site.errorMsg.value).toBeNull()
    scope.stop()
  })

  it('interleaves several forward rows around socket rows by time, without duplicates', async () => {
    const rows = [1, 2, 3, 4, 5].map((s) => record(`t${s}`, s, String(s)))
    api.getTranscript.mockResolvedValueOnce(page([], '0')).mockResolvedValueOnce(page(rows, '5'))
    const { site, scope } = mount()
    await site.load()
    await settle()

    site.receive(record('t2', 2))
    site.receive(record('t4', 4))
    await site.load()
    await settle()

    expect(ids(site.transcript.value)).toEqual(['t1', 't2', 't3', 't4', 't5'])
    scope.stop()
  })

  it('keeps the page order for rows that share one timestamp', async () => {
    const a = record('a', 3, '1')
    const b = record('b', 3, '2')
    api.getTranscript.mockResolvedValueOnce(page([], '0')).mockResolvedValueOnce(page([a, b], '2'))
    const { site, scope } = mount()
    await site.load()
    await settle()
    await site.load()
    await settle()
    expect(ids(site.transcript.value)).toEqual(['a', 'b'])
    scope.stop()
  })
})
