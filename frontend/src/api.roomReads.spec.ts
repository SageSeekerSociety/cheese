import type { ListPayload, TopicMemberRow } from './cx_types'

import { effectScope } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { afterEach, expect, it, vi } from 'vitest'

import { addTopicMember } from './api'

import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { roomMembersQuery } from '@/query/room'

function response(data: unknown) {
  return new Response(JSON.stringify({ code: 200, data }), { headers: { 'Content-Type': 'application/json' } })
}
const roster = (...handles: string[]) => ({ data: handles.map((member_handle) => ({ member_handle })) })
async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}
afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

// 房间名册开着（头部、成员面板、@ 候选读的都是它）。请人进来之前发出去的那次重读晚
// 回来，不能把刚请进来的人盖掉：那样他 @ 不出来，名册里也找不到。
it('a roster read that left before an invitation does not drop the new member', async () => {
  let late!: (value: Response) => void
  let reads = 0
  // 第一次读到 alice；第二次（重读）晚回来；请人之后的读到 alice 和 bob。
  const fetcher = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return Promise.resolve(response({ member_handle: 'bob' }))
    reads += 1
    if (reads === 1) return Promise.resolve(response(roster('alice')))
    if (reads === 2) return new Promise<Response>((yes) => (late = yes))
    return Promise.resolve(response(roster('alice', 'bob')))
  })
  vi.stubGlobal('fetch', fetcher)
  const scope = effectScope()
  const panel = scope.run(() => useQuery(roomMembersQuery('room'), queryClient))!
  try {
    await settle()
    void panel.refetch()
    await settle()
    await addTopicMember('room', 'bob')
    await settle()
    late(response(roster('alice')))
    await settle()
    const held = queryClient.getQueryData<ListPayload<TopicMemberRow>>(keys.roomMembers('room'))
    expect(held?.data.map((row) => row.member_handle)).toEqual(['alice', 'bob'])
  } finally {
    scope.stop()
  }
})
