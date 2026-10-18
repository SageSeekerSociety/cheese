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

  it('房间在等一位队友、等太久了：不画头像，行尾是一盏红灯', async () => {
    const room = topic('t-1', {
      waits: [{ member: CHEESE_SEAT, reason: 'failed', since: '2026-10-01T00:00:00Z' }],
    })
    const { rail } = await mountRail([room])

    expect(rail.memberMarks(room)).toEqual([])
    // 头像不画了，但「在等谁、为什么」还挂在灯上（悬停那一句）。
    expect(rail.lightOf('t-1')).toMatchObject({ kind: 'red', breathe: false, why: 'waiting' })
    expect(rail.lightOf('t-1')?.title).toContain('小知')
    expect(rail.lightOf('t-1')?.title).toContain('报错')
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

  it('等队友也是同一只钟：等满五分钟前不红，钟走过去就红', async () => {
    const base = Date.parse('2026-10-14T12:00:00Z')
    vi.setSystemTime(base)
    // 挂载这一刻，房间已经等了 4m51s——还差九秒。
    const since = new Date(base - 291_000).toISOString()
    const { rail } = await mountRail([topic('t-wait', { waits: [{ member: CHEESE_SEAT, reason: 'mention', since }] })])

    expect(rail.lightOf('t-wait')).toBeNull()

    vi.advanceTimersByTime(10_000)
    expect(rail.lightOf('t-wait')).toMatchObject({ kind: 'red', breathe: false, why: 'waiting' })
    expect(rail.lightOf('t-wait')?.title).toContain('有人 @ 了小知')
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

// 交付被退回不是「有事等你」：退回那一刻平台已经把球交回给队友，侧栏该红的红，
// 不该让人以为轮到自己了。
describe('交付被退回的任务行', () => {
  beforeEach(() => setLocale('zh-CN'))

  it('是红灯，不是黄灯——即使后端此刻还算作「等你」', async () => {
    const { rail } = await mountRail([topic('t-1', {})])
    const task = {
      id: 'k-1',
      room_id: 't-1',
      owner_handle: 'cheese-someone',
      awaits_me: true,
      presentation: { column: 'needs_you', phrase: 'bounced' },
    } as RailTask

    expect(rail.taskLight(task)).toEqual({ kind: 'red', breathe: false, why: 'bounced' })
  })
})

// 收起来的折叠开关替里面的行说了它们的状态（`toggleTitle`）。两种红是两件事，收起来
// 的那盏红说的是哪一件，开关就得说哪一件 —— 别把已经停下来报错的活说成还在跑。断的是
// 那句话的关键含义（在跑 vs 停下报错），不是整句原文。
describe('折叠开关替收起来的行说话', () => {
  beforeEach(() => {
    setLocale('zh-CN')
  })

  it('里面是「停下报错」的红，开关不说它「在跑」', async () => {
    const base = Date.now()
    const { rail } = await mountRail([
      topic('p', {}),
      // 子行收在 p 里面：它坏在这里，p 的灯和折叠开关都得替它说话。
      topic('c-stopped', {
        parent_id: 'p',
        incomplete_since: new Date(base - 10 * 60_000).toISOString(),
      }),
    ])

    const title = rail.toggleTitle(rail.railRows.value.find((r) => r.topic.id === 'p')!)
    expect(title).not.toContain('在跑')
    expect(title).toContain('报错')
  })

  it('里面是「在跑却没动静」的红，开关照旧说它没动静（没被上一条带偏）', async () => {
    const base = Date.now()
    const { rail } = await mountRail([
      topic('p', {}),
      topic('c-quiet', {
        parent_id: 'p',
        moving: true,
        last_output_at: new Date(base - 10 * 60_000).toISOString(),
      }),
    ])

    const title = rail.toggleTitle(rail.railRows.value.find((r) => r.topic.id === 'p')!)
    expect(title).toContain('在跑')
    expect(title).toContain('没动静')
  })
})
