/** 在名册抽屉里加人、移人之后，@ 候选立刻跟上，不用切走再切回来。
 *
 * 走的是真的 `api.ts`（加人、移人、拉名册都是它），只把网络换成一张内存里的名册。
 */
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { addTopicMember, removeTopicMember } from '../../api'

import { useRoomRoster } from './composables/useRoomRoster'

interface Seat {
  member_handle: string
  name: string
  agent: boolean
}

const PROJECT: ProjectMemberRow[] = [
  { user_handle: 'alice', name: 'Alice', source: 'owner' },
  { user_handle: 'bob', name: 'Bob', source: 'team' },
  { user_handle: 'cheese-planner', name: '规划师', agent: true },
  { user_handle: 'cheese-reviewer', name: '审稿人', agent: true },
] as ProjectMemberRow[]

const SEATS: Record<string, Seat> = {
  alice: { member_handle: 'alice', name: 'Alice', agent: false },
  bob: { member_handle: 'bob', name: 'Bob', agent: false },
  'cheese-planner': { member_handle: 'cheese-planner', name: '规划师', agent: true },
  'cheese-reviewer': { member_handle: 'cheese-reviewer', name: '审稿人', agent: true },
}

/** 每个话题的名册，后端那一份。 */
let rosters: Record<string, string[]>

function envelope(data: unknown) {
  return new Response(JSON.stringify({ code: 200, data }), { headers: { 'Content-Type': 'application/json' } })
}

function fakeBackend(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const url = new URL(raw, 'http://localhost')
  const method = (init?.method ?? 'GET').toUpperCase()
  const m = url.pathname.match(/\/topics\/([^/]+)\/members(?:\/([^/]+))?$/)
  if (!m) return Promise.resolve(envelope(null))
  const [, topicId, handle] = m
  const roster = (rosters[topicId] ??= [])
  if (method === 'POST') {
    const body = JSON.parse(String(init?.body)) as { handle: string }
    roster.push(body.handle)
    return Promise.resolve(envelope({ ...SEATS[body.handle], role: 'member' }))
  }
  if (method === 'DELETE') {
    rosters[topicId] = roster.filter((h) => h !== decodeURIComponent(handle))
    return Promise.resolve(envelope({ deleted: true }))
  }
  const data = roster.map((h) => SEATS[h])
  return Promise.resolve(envelope({ data, total: data.length }))
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function openRoom(topicId: string) {
  const scope = effectScope()
  const roster = scope.run(() =>
    useRoomRoster({
      topic: () => ({ id: topicId }) as Topic,
      members: () => PROJECT,
      author: 'alice',
      onError: () => {},
    })
  )!
  return { roster, scope }
}

beforeEach(() => {
  rosters = { t1: ['alice', 'cheese-planner'], t2: ['alice'] }
  vi.stubGlobal('fetch', vi.fn(fakeBackend))
})
afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('名册改了，@ 候选立刻跟上', () => {
  it('刚请进这个话题的 AI 队友马上 @ 得到', async () => {
    const { roster, scope } = openRoom('t1')
    await settle()
    expect(roster.mentionPool.value.map((p) => p.handle)).not.toContain('cheese-reviewer')

    await addTopicMember('t1', 'cheese-reviewer')
    await settle()

    expect(roster.mentionPool.value.map((p) => p.handle)).toContain('cheese-reviewer')
    scope.stop()
  })

  it('刚加进来的人不再挂「不在话题中」，刚移出去的人挂上', async () => {
    const { roster, scope } = openRoom('t1')
    await settle()
    const outside = () => roster.mentionPool.value.filter((p) => p.outsideTopic).map((p) => p.handle)
    expect(outside()).toEqual(['bob'])

    await addTopicMember('t1', 'bob')
    await settle()
    expect(outside()).toEqual([])

    await removeTopicMember('t1', 'bob')
    await settle()
    expect(outside()).toEqual(['bob'])
    scope.stop()
  })

  it('别的话题的名册改了，这个房间的候选不变', async () => {
    const { roster, scope } = openRoom('t2')
    await settle()
    const before = roster.mentionPool.value.map((p) => [p.handle, !!p.outsideTopic])

    await addTopicMember('t1', 'bob')
    await settle()

    expect(roster.mentionPool.value.map((p) => [p.handle, !!p.outsideTopic])).toEqual(before)
    scope.stop()
  })
})
