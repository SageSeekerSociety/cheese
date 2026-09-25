/** 本轮改动那一行：改了哪些文件、各改了多少，先看得到几个，其余一点就有。 */
import type { Component } from 'vue'
import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../../i18n'
import { collapseNotices } from '../../lib/platformNotice'

import RoomNotice from './RoomNotice.vue'

const vuetify = createVuetify({ components, directives })

function changes(paths: string[], omitted = 0): Block {
  return {
    id: 'c1',
    topic_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '改动',
    turn_id: 'turn-1',
    created_at: '2026-09-25T08:00:00Z',
    meta: {
      changeset: {
        files_total: paths.length + omitted,
        added: 0,
        removed: 0,
        files: paths.map((path, i) => ({ path, added: 10 + i, removed: i })),
        files_omitted: omitted,
      },
    },
  } as unknown as Block
}

function mount(block: Block) {
  const [row] = collapseNotices([
    block,
    { ...block, id: 'd1', content: '更新了文档', meta: { action: 'doc' } } as Block,
  ])
  return render(RoomNotice as Component, {
    props: {
      block: row.block,
      notice: row.notice!,
      run: row.run,
      name: '芝士',
      time: '16:05',
      agentName: '芝士',
      refs: { mentionNames: {}, topicTitles: {} },
    },
    global: { plugins: [vuetify] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('本轮改动的文件', () => {
  it('每个文件带着自己的增删', () => {
    const { container } = mount(changes(['a.ts', 'b.ts']))
    const text = container.textContent ?? ''
    expect(text).toContain('a.ts')
    expect(text).toContain('+10')
    expect(text).toContain('b.ts')
    expect(text).toContain('+11')
    expect(text).toContain('−1')
  })

  it('文件多的时候先列前几个，其余点一下就展开', async () => {
    const paths = ['a.ts', 'b.ts', 'c.ts', 'd.ts', 'e.ts']
    const { container, getByRole } = mount(changes(paths))
    expect(container.textContent).not.toContain('e.ts')
    await fireEvent.click(getByRole('button', { name: /另 \d+ 个文件/ }))
    for (const p of paths) expect(container.textContent).toContain(p)
  })

  it('后端没发过来的那几个也算进「另 N 个」，不假装已经列全', async () => {
    const { container, getByRole } = mount(changes(['a.ts', 'b.ts', 'c.ts', 'd.ts'], 8))
    expect(getByRole('button', { name: /另 9 个文件/ })).toBeTruthy()
    await fireEvent.click(getByRole('button', { name: /另 9 个文件/ }))
    expect(container.textContent).toContain('d.ts')
    expect(container.textContent).toContain('另 8 个文件')
  })
})
