import type { Component } from 'vue'
import type { SessionEnvironment, TopicEnvironment } from '../types/compute'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const machines = vi.hoisted(() => ({ get: vi.fn(), setSession: vi.fn(), setDefault: vi.fn() }))

// 服务端那份房间名册。加人（POST …/members）往里加一行；别的请求照常走（被网络守卫拦下）。
const ROOM = [
  { member_handle: 'alice', name: 'Alice', role: 'owner', agent: false, avatar_id: null },
  { member_handle: 'bob', name: 'Bob', role: 'member', agent: false, avatar_id: null },
  { member_handle: 'carol', name: 'Carol', role: 'member', agent: false, avatar_id: 77 },
  { member_handle: 'cheese-t1', name: '芝士', role: 'member', agent: true, avatar_id: null },
]
const server = vi.hoisted(() => ({ rows: [] as Record<string, unknown>[] }))

vi.mock('../api/http', async () => {
  const actual = await vi.importActual<typeof import('../api/http')>('../api/http')
  return {
    ...actual,
    request: (path: string, init?: RequestInit) => {
      if (path === '/topics/t1/members' && init?.method === 'POST') {
        const { handle } = JSON.parse(String(init.body)) as { handle: string }
        const row = { member_handle: handle, name: handle, role: 'member', agent: false, avatar_id: null }
        server.rows.push(row)
        return Promise.resolve(row)
      }
      return actual.request(path, init)
    },
  }
})

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getTopicEnvironment: (...args: unknown[]) => machines.get(...args),
    setSessionEnvironment: (...args: unknown[]) => machines.setSession(...args),
    setEnvironmentDefault: (...args: unknown[]) => machines.setDefault(...args),
    listTopicMembers: vi.fn(async () => ({ data: server.rows.map((row) => ({ ...row })), total: server.rows.length })),
  }
})

import { listTopicMembers } from '../api'
import { setLocale } from '../i18n'

import TopicMembers from './TopicMembers.vue'

import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

// 项目名册一张，队友也在上面（后端合的）：请一个队友进房间和请一个人是同一件事，
// 所以「添加」那张单子读的就是这一份，不再另外拉一份队友清单拼上去。
const PROJECT_MEMBERS = [
  { user_handle: 'alice', source: 'owner' as const, name: 'Alice', agent: false, active: true },
  { user_handle: 'bob', source: 'team' as const, name: 'Bob', agent: false, active: true },
  { user_handle: 'carol', source: 'external' as const, name: 'Carol', agent: false, active: true },
  { user_handle: 'dave', source: 'external' as const, name: 'Dave', agent: false, active: true },
  { user_handle: 'cheese-t1', source: 'agent' as const, name: '芝士', agent: true, active: true },
  { user_handle: 'cheese-a2', source: 'agent' as const, name: '评审', agent: true, active: true },
  { user_handle: 'cheese-a3', source: 'agent' as const, name: '退休', agent: true, active: false },
]

const Roster = TopicMembers as unknown as Component

// Vuetify 的浮层要这几样浏览器 API（happy-dom 没有）。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: {
        width: 1024,
        height: 768,
        offsetLeft: 0,
        offsetTop: 0,
        addEventListener() {},
        removeEventListener() {},
      },
    })
  }
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function openRoster(extra: Record<string, unknown> = {}) {
  const utils = render(Roster, {
    props: { topicId: 't1', projectId: 'p1', projectMembers: PROJECT_MEMBERS, me: 'alice', canManage: true, ...extra },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await settle()
  await fireEvent.click(document.querySelector('.members-mini')!)
  await settle()
  return utils
}

function roomEnvironments(overrides: Partial<TopicEnvironment> = {}): TopicEnvironment {
  return {
    default: { kind: 'sandbox', spec: 'standard' },
    default_from: 'project',
    sessions: [],
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true, owned: false }],
    specs: [
      { spec: 'standard', label: '标准' },
      { spec: 'vm', label: '虚拟机' },
    ],
    ...overrides,
  }
}

function session(id: string, seat: string, overrides: Partial<SessionEnvironment> = {}): SessionEnvironment {
  return {
    session_id: id,
    agent_handle: seat,
    seat_handle: seat,
    environment: { kind: 'sandbox', spec: 'standard' },
    status: 'ready',
    online: true,
    ...overrides,
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  server.rows = ROOM.map((row) => ({ ...row }))
  document.body.innerHTML = ''
  machines.get.mockReset().mockResolvedValue(roomEnvironments())
  machines.setSession.mockReset().mockResolvedValue({})
  machines.setDefault.mockReset().mockResolvedValue({})
})

describe('成员名册', () => {
  it('右键一位成员：移出，弹在鼠标那一点上', async () => {
    await openRoster()
    const bob = Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes('Bob'))!
    await fireEvent.contextMenu(bob, { clientX: 20, clientY: 40 })
    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) => el.textContent?.trim())
      ).toEqual(['移出频道'])
    )
  })

  it('不管这个频道的人只能看，不能加人也不能移人', async () => {
    await openRoster({ canManage: false })
    expect(document.querySelector('.roster__remove')).toBeNull()
    expect(document.querySelector('.roster__select')).toBeNull()
  })

  it('私密频道里的人不管频道也能拉人，但移不了人', async () => {
    await openRoster({ canManage: false, canInvite: true })
    expect(document.querySelector('.roster__select')).not.toBeNull()
    expect(document.querySelector('.roster__remove')).toBeNull()
  })

  it('「综合」里的人移不出去，只有 AI 队友能请进请出', async () => {
    await openRoster({ general: true })
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    const bobRow = rows.find((r) => r.textContent?.includes('Bob'))!
    const agent = rows.find((r) => r.textContent?.includes('cheese-t1'))!
    expect(bobRow.querySelector('.roster__remove')).toBeNull()
    expect(agent.querySelector('.roster__remove')).not.toBeNull()
    await fireEvent.mouseDown(document.querySelector('.roster__select .v-field')!)
    await settle()
    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).map((n) => n.textContent ?? '')
    expect(items.length).toBeGreaterThan(0)
    expect(items.every((t) => t.includes('AI 队友'))).toBe(true)
  })

  it('队友和人一样能被移出，但房间没有「换队友」这种开关', async () => {
    await openRoster()
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    expect(rows.length).toBe(4)

    const agentRow = rows.find((r) => r.textContent?.includes('cheese-t1'))!
    const humanRow = rows.find((r) => r.textContent?.includes('alice'))!
    expect(agentRow.querySelector('.roster__remove')).not.toBeNull()
    expect(humanRow.querySelector('.roster__remove')).not.toBeNull()
    expect(document.body.textContent).not.toMatch(/换队友|更换 AI 队友/)
  })

  it('「添加」列表里有还没进房间的队友，和人排在同一张单子上', async () => {
    await openRoster()
    await fireEvent.mouseDown(document.querySelector('.roster__select .v-field')!)
    await settle()
    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).map((n) => n.textContent ?? '')
    expect(items.some((t) => t.includes('评审') && t.includes('AI 队友'))).toBe(true)
    // 已经坐在房间里的那个不再列出来；停用的也不列。
    expect(items.some((t) => t.includes('芝士'))).toBe(false)
    expect(items.some((t) => t.includes('退休'))).toBe(false)
  })

  it('芝士不写角色 —— 它的身份是 Agent 那个标', async () => {
    await openRoster()
    const agentRow = Array.from(document.querySelectorAll('.roster__item')).find((r) =>
      r.textContent?.includes('cheese-t1')
    )!
    expect(agentRow.textContent).toContain('AI 队友')
    expect(agentRow.querySelector('.roster__role')).toBeNull()
  })

  it('管这个频道的人标着「管理者」', async () => {
    await openRoster()
    const rowOf = (handle: string) =>
      Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes(handle))!
    expect(rowOf('alice').textContent).toContain('管理者')
    expect(rowOf('bob').textContent).not.toContain('管理者')
  })

  // 换管理者只有项目设置那张频道表能做（清单第 5 条）：名单里没有那颗按钮，人得知道
  // 去哪儿找，所以这一行直说去处。
  it('管理者那一行写着去哪儿换人', async () => {
    await openRoster()
    const rowOf = (handle: string) =>
      Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes(handle))!
    expect(rowOf('alice').textContent).toContain('在项目设置的频道表里更换')
    expect(rowOf('bob').textContent).not.toContain('在项目设置的频道表里更换')
  })
})

// 频道详情的「设置」里那一行「成员」开的就是这一份，不在别处再造一张一样的名单。
it('外面叫它开，开的就是这一份名单', async () => {
  render(Roster, {
    props: {
      topicId: 't1',
      projectId: 'p1',
      projectMembers: PROJECT_MEMBERS,
      me: 'alice',
      canManage: true,
      open: true,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await settle()
  expect(document.querySelector('.roster')).not.toBeNull()
})

it('按钮上是一份名册：人数含 AI 队友，头像堆里没有单挂的那一颗', async () => {
  // 名册列表本来就是全量渲染的；这颗按钮曾经只数人、再把 AI 队友作为一张单独的
  // 头像挂在旁边，读起来像「几个人，另外还有个它」。头像堆后面不再另写一个总数：
  // 三张脸加「+1」已经说了是四位。
  await openRoster()
  expect(document.querySelectorAll('.members-mini__face:not(.members-mini__face--more)')).toHaveLength(3)
  expect(document.querySelector('.members-mini__face--more')!.textContent).toBe('+1')
  expect(document.querySelector('.members-mini__face--agent')).toBeNull()
  expect(document.querySelector('.members-mini')!.getAttribute('title')).toContain('4 位')
})

function faceOf(handle: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes(handle))!
  return row.querySelector('.roster__avatar') as HTMLElement
}

describe('名册上的头像', () => {
  it('没挑过头像的两个人是两种颜色 —— 一排一模一样的圆圈等于没有头像', async () => {
    await openRoster()
    const alice = faceOf('alice')
    const bob = faceOf('bob')
    expect(alice.textContent?.trim()).toBe('A')
    expect(bob.textContent?.trim()).toBe('B')
    expect(alice.style.backgroundColor).toBeTruthy()
    expect(bob.style.backgroundColor).toBeTruthy()
    expect(alice.style.backgroundColor).not.toBe(bob.style.backgroundColor)
  })

  it('挑过头像的人画他本人那张，不画首字母', async () => {
    await openRoster()
    // 图现在由 UserAvatar 画：根是那个席位方框，<img> 在它里面。
    const face = faceOf('carol')
    expect(face.querySelector('img')?.getAttribute('src')).toContain('/avatars/77')
    expect(face.querySelector('.user-avatar-char')).toBeNull()
  })
})

describe('外部成员在房间里', () => {
  it('名册上团队以外的人挂「外部」，团队里的人不挂', async () => {
    await openRoster()
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    const carol = rows.find((r) => r.textContent?.includes('Carol'))!
    const bob = rows.find((r) => r.textContent?.includes('Bob'))!
    expect(carol.textContent).toContain('外部')
    expect(bob.textContent).not.toContain('外部')
  })

  it('「添加」只从项目成员里挑，外部成员在单子上也标着「外部」', async () => {
    await openRoster()
    await fireEvent.mouseDown(document.querySelector('.roster__select .v-field')!)
    await settle()
    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).map((n) => n.textContent ?? '')
    const dave = items.find((t) => t.includes('Dave'))!
    expect(dave).toContain('外部')
    // 单子上只有项目名册里还没进房间的人和队友，没有别的来源。
    expect(items.filter((t) => !t.includes('AI 队友'))).toEqual([dave])
  })
})

function agentRow(): Element {
  return Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes('cheese-t1'))!
}

function rowOf(handle: string): Element {
  return Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes(handle))!
}

// 在打开的那张环境菜单里选一种沙箱规格、点「使用这个环境」。
async function pickSpec(spec: string) {
  const radio = await waitFor(() => {
    const el = document.querySelector<HTMLInputElement>(`[data-testid="env-spec-${spec}"] input`)
    expect(el).not.toBeNull()
    return el!
  })
  await fireEvent.update(radio)
  await fireEvent.click(document.querySelector('[data-testid="env-submit"]')!)
  await settle()
}

describe('名册上每条会话的执行环境', () => {
  // 每条会话有自己的执行环境（#2320）：换一位 AI 队友的，只搬它这一条。
  it('在 A 那一行换环境，只换 A 的会话，B 的不动', async () => {
    server.rows = [
      ...ROOM.map((row) => ({ ...row })),
      { member_handle: 'cheese-a2', name: '评审', role: 'member', agent: true, avatar_id: null },
    ]
    machines.get.mockResolvedValue(
      roomEnvironments({ sessions: [session('s-a', 'cheese-t1'), session('s-b', 'cheese-a2')] })
    )
    await openRoster()
    const rowA = document.querySelector('[data-testid="session-env-s-a"]')!
    expect(rowA.closest('.roster__item')).toBe(rowOf('cheese-t1'))
    await fireEvent.click(rowA.querySelector('.env-action')!)
    await pickSpec('vm')

    await waitFor(() => expect(machines.setSession).toHaveBeenCalledTimes(1))
    expect(machines.setSession).toHaveBeenCalledWith('t1', 's-a', { kind: 'sandbox', spec: 'vm' })
    expect(machines.setDefault).not.toHaveBeenCalled()
  })

  it('点开再取消，什么也不发', async () => {
    machines.get.mockResolvedValue(roomEnvironments({ sessions: [session('s-a', 'cheese-t1')] }))
    await openRoster()
    await fireEvent.click(document.querySelector('[data-testid="session-env-s-a"] .env-action')!)
    const radio = await waitFor(() => {
      const el = document.querySelector<HTMLInputElement>('[data-testid="env-spec-vm"] input')
      expect(el).not.toBeNull()
      return el!
    })
    await fireEvent.update(radio)
    await fireEvent.click(document.querySelector('[data-testid="env-cancel"]')!)
    await settle()
    expect(machines.setSession).not.toHaveBeenCalled()
    expect(machines.setDefault).not.toHaveBeenCalled()
  })

  it('还没开工的队友没有会话，那一行不写环境', async () => {
    await openRoster()
    expect(agentRow().querySelector('[data-testid^="session-env-"]')).toBeNull()
  })

  it('「新会话默认」那一行改的是默认，不搬任何一条会话', async () => {
    machines.get.mockResolvedValue(roomEnvironments({ sessions: [session('s-a', 'cheese-t1')] }))
    await openRoster()
    const row = document.querySelector('[data-testid="future-machine"]')!
    expect(row.textContent).toContain('新 AI 队友默认：沙箱 · 标准')
    expect(row.textContent).toContain('项目默认')
    await fireEvent.click(row.querySelector('.env-action')!)
    await pickSpec('vm')
    await waitFor(() => expect(machines.setDefault).toHaveBeenCalledWith('t1', { kind: 'sandbox', spec: 'vm' }))
    expect(machines.setSession).not.toHaveBeenCalled()
  })
})

it('回到看过的频道：名册先画上次那份，重取的时候不换成骨架', async () => {
  const first = await openRoster()
  expect(document.body.textContent).toContain('Bob')
  first.unmount()
  document.body.innerHTML = ''

  // 离开之后名册过了新鲜期，回来要重取；这一次的重取一直没回来：屏幕上该是上次那份，
  // 不是一片骨架。
  await queryClient.invalidateQueries({ queryKey: keys.roomMembers('t1'), refetchType: 'none' })
  vi.mocked(listTopicMembers).mockImplementationOnce(() => new Promise(() => {}))
  await openRoster()

  expect(listTopicMembers).toHaveBeenLastCalledWith('t1')
  expect(document.body.textContent).toContain('Bob')
})

it('加一位进来，名册当场多他一行，不用重开房间', async () => {
  await openRoster()
  expect(document.body.textContent).not.toContain('dave')

  await fireEvent.mouseDown(document.querySelector('.roster__select .v-field')!)
  await settle()
  const dave = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).find((n) =>
    n.textContent?.includes('Dave')
  ) as HTMLElement
  await fireEvent.click(dave)
  await settle()
  const add = Array.from(document.querySelectorAll('.roster__add button')).find(
    (b) => b.textContent?.trim() === '加入'
  ) as HTMLElement
  await fireEvent.click(add)

  await waitFor(() =>
    expect(Array.from(document.querySelectorAll('.roster__item')).some((r) => r.textContent?.includes('dave'))).toBe(
      true
    )
  )
})

describe('队友名字下面那串 handle', () => {
  // 项目成员页在队友名字下面写它自己的 handle（`cheese`），频道名册和「添加」下拉写的
  // 得是同一串，而不是座位账号（`cheese-<hex>`）。
  it('名册和「添加」下拉都写队友自己的 handle，不写座位账号', async () => {
    server.rows = ROOM.map((row) =>
      row.agent ? { ...row, member_handle: 'cheese-68ec1f2dbcad', instance_handle: 'cheese' } : { ...row }
    )
    const members = PROJECT_MEMBERS.map((m) =>
      m.user_handle === 'cheese-a2' ? { ...m, instance_handle: 'reviewer' } : m
    )
    await openRoster({ projectMembers: members })

    const handles = Array.from(document.querySelectorAll('.roster__item .roster__handle')).map((el) =>
      el.textContent?.trim()
    )
    expect(handles).toContain('cheese')
    expect(handles).not.toContain('cheese-68ec1f2dbcad')

    await fireEvent.mouseDown(document.querySelector('.roster__select .v-field')!)
    await settle()
    const options = Array.from(document.querySelectorAll('.v-overlay .roster__handle')).map((el) =>
      el.textContent?.trim()
    )
    expect(options).toContain('reviewer')
    expect(options).not.toContain('cheese-a2')
  })
})

describe('名册一打开先有内容', () => {
  // 打开名册是点下去之后第一个有画面的动作：整份名单一次建出来，几十行在平板上要等
  // 几秒，而列表只露得出五六行。先挂看得见的那几行，其余等浏览器空闲再补——第一帧
  // 必须有内容，否则看起来像没点到。
  const manyMembers = (count: number) =>
    Array.from({ length: count }, (_, i) => ({
      member_handle: `member-${i}`,
      name: `成员${i}`,
      role: 'member',
      agent: false,
      avatar_id: null,
    }))

  it('打开那一帧只挂前几行，其余的分批补上', async () => {
    server.rows = manyMembers(30)

    // 把空闲回调攥在手里：补行排在里面，什么时候跑由这个用例决定。
    const idle: Array<() => void> = []
    const scope = globalThis as {
      requestIdleCallback?: (cb: () => void) => number
      cancelIdleCallback?: (id: number) => void
    }
    const original = scope.requestIdleCallback
    const originalCancel = scope.cancelIdleCallback
    scope.requestIdleCallback = (cb: () => void) => idle.push(cb)
    scope.cancelIdleCallback = (id: number) => {
      idle[id - 1] = () => {}
    }
    try {
      await openRoster()
      expect(document.querySelectorAll('.roster__item')).toHaveLength(8)

      while (idle.length) idle.shift()!()
      await settle()
      expect(document.querySelectorAll('.roster__item')).toHaveLength(30)
    } finally {
      if (original) scope.requestIdleCallback = original
      else delete scope.requestIdleCallback
      if (originalCancel) scope.cancelIdleCallback = originalCancel
      else delete scope.cancelIdleCallback
    }
  })

  // 频道详情的「设置」里那一份一挂上就是开着的：没有「变成开」的那一下，补行也得跑。
  it('一挂上就开着的名单，剩下的行照样补上', async () => {
    server.rows = manyMembers(30)
    render(Roster, {
      props: {
        topicId: 't1',
        projectId: 'p1',
        projectMembers: PROJECT_MEMBERS,
        me: 'alice',
        canManage: true,
        open: true,
      },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await settle()
    expect(document.querySelectorAll('.roster__item')).toHaveLength(30)
  })
})
