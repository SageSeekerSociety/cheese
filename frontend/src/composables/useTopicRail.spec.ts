// 侧栏一行右边那几个小头像：同一位队友只画一次。
//
// 一位队友在记录里有两个 handle——名册上的座位（`cheese-<hex>`）和它自己的（`cheese`，
// 会话、轮次帧、消息的收件人用它）。「在干活」和「房间在等它」两处各写一个，是同一个
// 人的两条记录，不是两个人：按 handle 比字符串去重，它就会被画成两个头像，一绿一红。
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

/** 挂一条侧栏，把每一行画出来的头像（状态 + 名字）摆进 DOM。 */
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
            rail
              .memberMarks(room)
              .map((mark) => h('span', { 'data-room': room.id, 'data-state': mark.state, 'data-name': mark.name }))
          )
        )
    },
  })
  const view = render(Host, { global: { plugins: [pinia] } })
  await nextTick()
  return view
}

const marksOf = (container: Element, room: string) =>
  [...container.querySelectorAll(`[data-room="${room}"]`)].map((el) => ({
    state: el.getAttribute('data-state'),
    name: el.getAttribute('data-name'),
  }))

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

  it('同一位在干活、又被等着，只画一个——而且是红色那个', async () => {
    const { container } = await mount([
      topic('t-1', {
        activity: [{ member: 'cheese', kind: 'working', since: 1 }],
        waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
      }),
    ])

    expect(marksOf(container, 't-1')).toEqual([{ state: 'stalled', name: '小知' }])
  })

  it('两位队友各画一个，在等的那位在前', async () => {
    const { container } = await mount([
      topic('t-1', {
        activity: [{ member: 'cheese-kimi', kind: 'working', since: 1 }],
        waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
      }),
    ])

    expect(marksOf(container, 't-1')).toEqual([
      { state: 'stalled', name: '小知' },
      { state: 'working', name: '芝士K' },
    ])
  })
})

// 「停下来报错」那盏红灯是**时间自己走到**的，不是数据变出来的：列表三十秒才刷一次，
// 而等满五分钟靠的是侧栏自己那只每 10 秒拨一下的慢钟（`useTopicRail.ts` 的 `clock`）。
// 这里就拨那只钟：数据一个字节没动，只让系统时间往前走过五分钟，红灯就该自己亮。
describe('侧栏自己的慢钟把红灯拨亮', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setLocale('zh-CN')
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('频道行：满五分钟前不红，钟自己走过五分钟就红', async () => {
    const base = Date.parse('2026-10-14T12:00:00Z')
    vi.setSystemTime(base)
    // 挂载这一刻，这间房已经坏了 4m51s——还差九秒。
    const incompleteSince = new Date(base - 291_000).toISOString()
    const { rail } = await mountRail([topic('t-stuck', { incomplete_since: incompleteSince })])

    expect(rail.lightOf('t-stuck')).toBeNull()

    // 侧栏那只 10 秒的慢钟拨一下：now -> base+10s，坏了的时长 301s > 5 分钟。
    vi.advanceTimersByTime(10_000)
    expect(rail.lightOf('t-stuck')).toEqual({ kind: 'red', breathe: false, why: 'unfinished' })
  })

  it('任务行：同一只钟，活自己那一行也一样', async () => {
    const base = Date.parse('2026-10-14T12:00:00Z')
    vi.setSystemTime(base)
    const incompleteSince = new Date(base - 291_000).toISOString()
    const { rail } = await mountRail([topic('t-1', {})])
    // 别人的活、状态那几盏都没话说：不补蓝灯，此刻也不红。
    const task = {
      id: 'k-1',
      room_id: 't-1',
      owner_handle: 'cheese-someone',
      incomplete_since: incompleteSince,
    } as RailTask

    expect(rail.taskLight(task)).toBeNull()

    vi.advanceTimersByTime(10_000)
    expect(rail.taskLight(task)).toEqual({ kind: 'red', breathe: false, why: 'unfinished' })
  })
})
