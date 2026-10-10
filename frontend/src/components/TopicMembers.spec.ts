import type { Component } from 'vue'
import type { ComputeChoice, TopicComputeProfile } from '../types/compute'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const machines = vi.hoisted(() => ({ get: vi.fn() }))

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
    getTopicComputeProfile: (...args: unknown[]) => machines.get(...args),
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

// Vuetify 的浮层要这几样浏览器 API（happy-dom 没有），同 TopicComputePicker.spec。
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

const CLOUD: ComputeChoice = {
  name: null,
  profile: 'cloud',
  device_id: null,
}
const LAB: ComputeChoice = { ...CLOUD, profile: 'device', device_id: null }

function roomMachines(overrides: Partial<TopicComputeProfile> = {}): TopicComputeProfile {
  return {
    choice: CLOUD,
    project_default: CLOUD,
    current: 'cloud',
    device_id: null,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true }],
    sessions: [],
    cloud_vm_available: false,
    profiles: [],
    visibility: { options: [], effective: null, machine_access: false },
    ...overrides,
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  server.rows = ROOM.map((row) => ({ ...row }))
  document.body.innerHTML = ''
  machines.get.mockReset().mockResolvedValue(roomMachines())
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

describe('名册上这个话题的工作电脑', () => {
  // 一个话题一个容器（2026-09-28，推翻结论 60）：房间里的 AI 队友都在同一台上，所以
  // 名册只在底下写一行房间的，队友那一行不再各写一台。
  it('房间里坐着几条会话，也只有房间那一行，队友那一行不写工作电脑', async () => {
    const session = (id: string) => ({
      id,
      agent_handle: 'cheese-t1',
      harness: `harness-${id}`,
      choice: LAB,
      lease: { device_id: 'lab', generation: 1, status: 'ready', online: true },
      machine_access: true,
    })
    machines.get.mockResolvedValue(
      roomMachines({
        choice: { ...LAB, name: '实验室工作站', device_id: 'lab' },
        sessions: [session('s1'), session('s2')],
      })
    )
    await openRoster()
    expect(document.querySelectorAll('[data-testid="agent-machine"]')).toHaveLength(0)
    expect(agentRow().textContent).not.toContain('环境')
    const rooms = document.querySelectorAll('[data-testid="future-machine"]')
    expect(rooms).toHaveLength(1)
    expect(rooms[0].textContent).toContain('本频道运行在：实验室工作站')
    expect(rooms[0].textContent).toContain('改')
  })

  it('房间那一行跟着项目默认时标出来', async () => {
    await openRoster()
    const room = document.querySelector('[data-testid="future-machine"]')!
    expect(room.textContent).toContain('本频道运行在：云端环境')
    expect(room.textContent).toContain('项目默认')
  })

  it('房间那台能访问整台电脑时，提醒挂在房间那一行上', async () => {
    machines.get.mockResolvedValue(
      roomMachines({
        choice: { ...LAB, name: '实验室工作站', device_id: 'lab' },
        visibility: {
          options: [],
          effective: 'host',
          machine_access: true,
        },
      })
    )
    await openRoster()
    const room = document.querySelector('[data-testid="future-machine"]')!
    expect(room.textContent).toContain('能访问整台电脑')
    expect(room.textContent).not.toContain('项目默认')
  })

  it('有队友能访问整台电脑时告诉页头，名册合着也看得见', async () => {
    machines.get.mockResolvedValue(
      roomMachines({
        visibility: {
          options: [],
          effective: 'host',
          machine_access: true,
        },
      })
    )
    const { emitted } = render(Roster, {
      props: { topicId: 't1', projectId: 'p1', projectMembers: PROJECT_MEMBERS, me: 'alice' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await settle()
    const notices = emitted()['machine-access'] as [string | null][]
    expect(notices.at(-1)).toEqual(['让它看到整台电脑（能操作这台电脑上的服务和其他频道）'])
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
