/** 「外部」标在聊天里：说话的人、@ 候选里的人，凡是团队以外、被邀请进这个项目的，
 * 都要看得出来。判据只有一个——项目名册上他那一行是 `source: 'external'`。
 */
import type { Component } from 'vue'
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return { ...actual, listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })) }
})

import { setLocale } from '../../i18n'

import { useRoomRoster } from './composables/useRoomRoster'
import RoomMessage from './RoomMessage.vue'

const MEMBERS: ProjectMemberRow[] = [
  { user_handle: 'alice', name: 'Alice', source: 'owner' },
  { user_handle: 'bob', name: 'Bob', source: 'team' },
  { user_handle: 'carol', name: 'Carol', source: 'external' },
]

function roster() {
  return useRoomRoster({
    topic: () => ({ id: 't1' }) as Topic,
    members: () => MEMBERS,
    author: 'alice',
    onError: () => {},
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('聊天里的外部成员', () => {
  it('名册上是外部成员的人才被认成外部', () => {
    const r = roster()
    expect(r.isExternal('carol')).toBe(true)
    expect(r.isExternal('bob')).toBe(false)
    expect(r.isExternal('nobody')).toBe(false)
  })

  it('@ 候选里带着外部这一位', () => {
    const r = roster()
    const pool = r.mentionPool.value
    expect(pool.find((p) => p.handle === 'carol')?.external).toBe(true)
    expect(pool.find((p) => p.handle === 'bob')?.external).toBe(false)
  })

  it('外部成员发的消息，名字旁边挂「外部」', () => {
    const base = {
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      avatar: null,
      isAgent: false,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      viewer: 'alice',
      pickerOpen: false,
      askBusy: false,
    }
    const block = { id: 'b1', author: 'carol', content: 'hi', kind: 'message', created_at: '' }
    const vuetify = createVuetify({ components, directives })
    const out = render(RoomMessage as unknown as Component, {
      props: { ...base, block, authorName: 'Carol', external: true },
      global: { plugins: [vuetify] },
    })
    expect(out.container.querySelector('.im-meta')?.textContent).toContain('外部')
    out.unmount()
    const inside = render(RoomMessage as unknown as Component, {
      props: { ...base, block: { ...block, author: 'bob' }, authorName: 'Bob', external: false },
      global: { plugins: [vuetify] },
    })
    expect(inside.container.querySelector('.im-meta')?.textContent).not.toContain('外部')
  })
})
