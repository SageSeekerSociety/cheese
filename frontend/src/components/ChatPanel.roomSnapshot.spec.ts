// 打开房间的导航出发时记下「快照要来」；订阅生效时 `subscribed` 带着房间的样子，对话栏
// 把它放进缓存，名册这些不再各发一个请求。
import type { Component } from 'vue'
import type { Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn(),
  listTopicMembers: vi.fn(),
}))

import { listBlocks, listTopicMembers } from '../api'

import ChatPanel from './ChatPanel.vue'

import i18n from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { expectRoom } from '@/query/snapshot'

const sockets: TestSocket[] = []
class TestSocket {
  readyState = 1
  newest: number | null = null
  room: unknown = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  drop = vi.fn()
  constructor() {
    sockets.push(this)
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  sockets.length = 0
  vi.stubGlobal('WebSocket', TestSocket)
  vi.mocked(listBlocks)
    .mockReset()
    .mockResolvedValue({ data: [], has_more: false, total: 0, oldest_id: null, has_newer: false, newest_id: null })
  vi.mocked(listTopicMembers).mockReset()
})

it('a room opened with its snapshot draws its roster without asking for it', async () => {
  const room = crypto.randomUUID()
  expectRoom(room, room)
  render(ChatPanel as unknown as Component, {
    props: { topic: { id: room, project_id: 'p', title: 'R', kind: 'topic' } as Topic, showComposer: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await vi.advanceTimersByTimeAsync(0)
  const socket = sockets.at(-1)!
  socket.room = {
    members: [{ topic_id: room, member_handle: 'alice', role: 'owner' }],
    feedback_proposals: [],
    skill_proposals: [],
    tasks: { open: [], recent: [] },
    pins: [],
    threads: [],
  }
  socket.onopen?.()
  await vi.advanceTimersByTimeAsync(0)

  const roster = queryClient.getQueryData<{ data: { member_handle: string }[] }>(keys.roomMembers(room))
  expect(roster?.data.map((row) => row.member_handle)).toEqual(['alice'])
  expect(listTopicMembers).not.toHaveBeenCalled()
})
