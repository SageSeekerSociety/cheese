// 支线页那一格（纯展示）：开着的在上，收起来的在下面「已回收」一栏里。
//
// 收起来的是报错满三天、没人回来的支线（`thread/recycle.py`）。它平时收着，点一下才
// 摊开；摊开之后每一行都读得出来、点得进去，能恢复的人旁边有一颗「恢复」，按下去是把
// 意图抛出去，不是自己去发请求。
import type { ThreadRow } from '@/types/threads'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import PanelThreads from './PanelThreads.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

function row(id: string, recycled: string | null = null, openedBy = 'alice'): ThreadRow {
  return {
    id,
    room_id: 'room1',
    root_block_id: `root-${id}`,
    reply_count: 2,
    last_reply_at: '2026-10-20T00:00:00Z',
    last_reply: null,
    participants: ['bob', 'alice'],
    created_by: openedBy,
    tasks: [],
    root: null,
    unread: false,
    recycled_at: recycled,
  } as unknown as ThreadRow
}

function open(rows: ThreadRow[], recycled: ThreadRow[], canRestore?: (r: ThreadRow) => boolean) {
  return render(PanelThreads, {
    props: {
      rows,
      recycled,
      loading: false,
      error: null,
      refs: { mentionNames: {}, topicTitles: {} },
      nameOf: (handle: string) => handle,
      fmtTime: (iso: string) => iso,
      canCreate: true,
      canRestore,
    },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('支线页的「已回收」那一栏', () => {
  it('没有收起来的支线时，那一栏根本不出现', () => {
    const { queryByTestId, getAllByTestId } = open([row('t1')], [])
    expect(queryByTestId('recycled-toggle')).toBeNull()
    expect(getAllByTestId('thread-row')).toHaveLength(1)
  })

  it('平时收着，点一下才摊开那几条，并说它为什么在那儿', async () => {
    const { getByTestId, queryAllByTestId } = open([row('t1')], [row('t2', '2026-10-26T00:00:00Z')])
    expect(queryAllByTestId('recycled-row')).toHaveLength(0)

    await fireEvent.click(getByTestId('recycled-toggle'))

    expect(queryAllByTestId('recycled-row')).toHaveLength(1)
    expect(getByTestId('recycled-toggle').textContent).toContain('已回收 · 1')
  })

  it('点一条收起来的支线，是把它打开来读 —— 不是恢复它', async () => {
    const { getByTestId, queryAllByTestId, emitted } = open([], [row('t2', '2026-10-26T00:00:00Z')])
    await fireEvent.click(getByTestId('recycled-toggle'))

    await fireEvent.click(getByTestId('recycled-row'))

    expect(emitted('open')).toEqual([['t2']])
    expect(emitted('restore')).toBeUndefined()
    expect(queryAllByTestId('recycled-row')).toHaveLength(1)
  })

  it('「恢复」只有能恢复的人那儿才画，点它是抛一件事出去', async () => {
    const seen: string[] = []
    const { getByTestId, queryByTestId, emitted } = open([], [row('t2', '2026-10-26T00:00:00Z')], (r) => {
      seen.push(r.id)
      return r.id === 't2'
    })
    expect(queryByTestId('restore-thread')).toBeNull()
    await fireEvent.click(getByTestId('recycled-toggle'))

    await fireEvent.click(getByTestId('restore-thread'))

    expect(seen).toContain('t2')
    expect(emitted('restore')).toEqual([['t2']])
  })

  it('不能恢复的那一条旁边没有「恢复」', async () => {
    const { getByTestId, queryByTestId } = open([], [row('t2', '2026-10-26T00:00:00Z')], () => false)
    await fireEvent.click(getByTestId('recycled-toggle'))

    expect(queryByTestId('restore-thread')).toBeNull()
  })
})
