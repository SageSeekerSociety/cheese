// 侧栏一行右边那几个小头像：只画此刻在这个房间里干活的队友，同一位只画一次。
//
// 房间在等一位队友、等太久了不再是头像：那是这一行的状态，由行尾那盏红灯说
// （`lib/roomLight.ts` 的 `waiting`），和任务行同一颗点、同一个规矩。
//
// 一位队友在记录里有两个 handle——名册上的座位（`cheese-<hex>`）和它自己的（`cheese`，
// 会话、轮次帧、消息的收件人用它）。同一位的两条「在干活」记录不是两个人：按 handle
// 比字符串去重，否则会被画成两个头像。
import type { RailTask } from '@/components/topic-sidebar/moreTasks'
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { defineComponent, h, nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTopicRail } from './useTopicRail'

import { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

const CHEESE_SEAT = 'cheese-0000000000a1'
const KIMI_SEAT = 'cheese-0000000000b2'

// 两位有名字的队友：默认那位在记录里用 `cheese`（它自己的 handle），另一位用
// `cheese-kimi`。名字都改过，所以屏幕上的名字就是名册上的名字，不是「芝士」兜底。
const TEAM: ProjectMemberRow[] = [
  {
    user_handle: CHEESE_SEAT,
    instance_handle: 'cheese',
    name: '小知',
    name_source: 'human',
    agent: true,
    project_default: true,
    source: 'agent',
  },
  {
    user_handle: KIMI_SEAT,
    instance_handle: 'cheese-kimi',
    name: '芝士K',
    name_source: 'human',
    agent: true,
    source: 'agent',
  },
]

function topic(id: string, extra: Partial<Topic>): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: id,
    kind: 'topic',
    status: 'active',
    created_at: '2026-10-01T00:00:00Z',
    ...extra,
  } as Topic
}

/** 挂一条侧栏，把每一行画出来的头像（名字）摆进 DOM。 */
async function mount(topics: Topic[]) {
  // 名册在项目缓存里：打开 p1、名册已经读回来。
  const pinia = createPinia()
  setActivePinia(pinia)
  seedProjects([])
  seedProject('p1', { topics, members: TEAM, unread: {}, privateUnread: {}, notifyLevels: {} })
  useWorkspaceStore().openProject('p1')
  const Host = defineComponent({
    setup() {
      const rail = useTopicRail({ topics, selectedProjectId: 'p1', selectedTopicId: null })
      return () =>
        h(
          'div',
          topics.flatMap((room) =>
            rail.memberMarks(room).map((mark) => h('span', { 'data-room': room.id, 'data-name': mark.name }))
          )
        )
    },
  })
  const view = render(Host, { global: { plugins: [pinia] } })
  await nextTick()
  return view
}

const marksOf = (container: Element, room: string) =>
  [...container.querySelectorAll(`[data-room="${room}"]`)].map((el) => el.getAttribute('data-name'))

/** 挂一条侧栏，并把 rail 实例交出来，好直接问 lightOf / taskLight。 */
async function mountRail(topics: Topic[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  seedProjects([])
  seedProject('p1', { topics, members: TEAM, unread: {}, privateUnread: {}, notifyLevels: {} })
  useWorkspaceStore().openProject('p1')
  let rail!: ReturnType<typeof useTopicRail>
  const Host = defineComponent({
    setup() {
      rail = useTopicRail({ topics, selectedProjectId: 'p1', selectedTopicId: null })
      return () => h('div')
    },
  })
  const view = render(Host, { global: { plugins: [pinia] } })
  await nextTick()
  return { rail, view }
}

describe('侧栏一行上的成员头像', () => {
  beforeEach(() => setLocale('zh-CN'))

  it('同一位在干活、又被等着：头像不画，只有行尾的红灯（不再一绿一红）', async () => {
    const { container } = await mount([
      topic('t-1', {
        activity: [{ member: 'cheese', kind: 'working', since: 1 }],
        waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
      }),
    ])

    expect(marksOf(container, 't-1')).toEqual([])
  })

  it('房间在等一位队友、等太久了：不画头像，也不画报错红灯', async () => {
    const room = topic('t-1', {
      waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
    })
    const { rail } = await mountRail([room])

    expect(rail.memberMarks(room)).toEqual([])
    expect(rail.lightOf('t-1')).toBeNull()
  })

  it('只画此刻在干活的成员', async () => {
    const { container } = await mount([
      topic('t-1', {
        activity: [{ member: 'cheese-kimi', kind: 'working', since: 1 }],
        waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
      }),
    ])

    expect(marksOf(container, 't-1')).toEqual(['芝士K'])
  })
})

describe('报错与等待超时不再在侧栏亮红灯', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setLocale('zh-CN')
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('频道行与任务行：未恢复报错和等待超时不亮红灯', async () => {
    const base = Date.parse('2026-10-14T12:00:00Z')
    vi.setSystemTime(base)
    const incompleteSince = new Date(base - 291_000).toISOString()
    const { rail } = await mountRail([
      topic('t-stuck', { incomplete_since: incompleteSince }),
      topic('t-wait', { waits: [{ member: CHEESE_SEAT, reason: 'mention', since: incompleteSince }] }),
    ])
    const task = {
      id: 'k-1',
      room_id: 't-stuck',
      owner_handle: 'cheese-someone',
      incomplete_since: incompleteSince,
    } as RailTask

    vi.advanceTimersByTime(10_000)
    expect(rail.lightOf('t-stuck')).toBeNull()
    expect(rail.lightOf('t-wait')).toBeNull()
    expect(rail.taskLight(task)).toBeNull()
  })
})

// 交付被退回不是「有事等你」：退回那一刻平台已经把球交回给队友，不该让人以为轮到自己了。
describe('交付被退回的任务行', () => {
  beforeEach(() => setLocale('zh-CN'))

  it('不亮黄灯——即使后端此刻还算作「等你」', async () => {
    const { rail } = await mountRail([topic('t-1', {})])
    const task = {
      id: 'k-1',
      room_id: 't-1',
      owner_handle: 'cheese-someone',
      awaits_me: true,
      presentation: { column: 'needs_you', phrase: 'bounced' },
    } as RailTask

    expect(rail.taskLight(task)).toBeNull()
  })
})

// 有活在往前走：绿灯在闪，而且压过「做完了、还没看过」那一档——同一行两件事都成立时，
// 先说「还在走」。
describe('呼吸绿', () => {
  beforeEach(() => setLocale('zh-CN'))

  it('频道行有活在往前走：绿灯在闪', async () => {
    const { rail } = await mountRail([topic('t-moving', { moving: true })])

    expect(rail.lightOf('t-moving')).toEqual({ kind: 'green', breathe: true })
  })

  it('任务行的会话在跑或走在交付链路上：闪绿压过长绿', async () => {
    const { rail } = await mountRail([topic('t-1', {})])
    const task = {
      id: 'k-1',
      room_id: 't-1',
      owner_handle: 'cheese-someone',
      moving: true,
      unseen_delivery: true,
      presentation: { column: 'delivering', phrase: 'awaiting_checks' },
    } as RailTask

    expect(rail.taskLight(task)).toEqual({ kind: 'green', breathe: true })
  })
})

// 收起来的折叠开关替里面的行说了它们的状态（`toggleTitle`）。
describe('折叠开关替收起来的行说话', () => {
  beforeEach(() => {
    setLocale('zh-CN')
  })

  it('里面只有停下报错的子行时，开关不报「在跑」', async () => {
    const base = Date.now()
    const { rail } = await mountRail([
      topic('p', {}),
      topic('c-stopped', {
        parent_id: 'p',
        incomplete_since: new Date(base - 10 * 60_000).toISOString(),
      }),
    ])

    const title = rail.toggleTitle(rail.railRows.value.find((r) => r.topic.id === 'p')!)
    expect(title).not.toContain('在跑')
  })

  it('里面是「有活在往前走」的闪绿，开关说它在走，不说只是刚交付完', async () => {
    const { rail } = await mountRail([
      topic('p', {}),
      // 子行还在往前走：收起来之后，父行的折叠开关要替它说这一件。
      topic('c-moving', { parent_id: 'p', moving: true }),
    ])

    const title = rail.toggleTitle(rail.railRows.value.find((r) => r.topic.id === 'p')!)
    expect(title).toContain('正在进行')
  })
})
