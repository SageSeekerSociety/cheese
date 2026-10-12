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
const server = vi.hoisted(() => ({
  rows: [] as Record<string, unknown>[],
  failFor: new Set<string>(),
  // 每一次请人的尝试，记成 `topicId:handle` —— 用来断言「请不成的才重试」「切了频道
  // 之后整批仍进原来的频道」。
  attempts: [] as string[],
  // 把请人那个来回挂住：切频道要卡在它中间。
  hold: null as null | { promise: Promise<void>; release: () => void },
}))

vi.mock('../api/http', async () => {
  const actual = await vi.importActual<typeof import('../api/http')>('../api/http')
  return {
    ...actual,
    request: (path: string, init?: RequestInit) => {
      const invite = /^\/topics\/([^/]+)\/members$/.exec(path)
      if (invite && init?.method === 'POST') {
        const topicId = invite[1]
        const { handle } = JSON.parse(String(init.body)) as { handle: string }
        server.attempts.push(`${topicId}:${handle}`)
        if (server.failFor.has(handle)) return Promise.reject(new Error('邀请失败'))
        const commit = () => {
          const row = { member_handle: handle, name: handle, role: 'member', agent: false, avatar_id: null }
          server.rows.push(row)
          return row
        }
        return server.hold ? server.hold.promise.then(commit) : Promise.resolve(commit())
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

// 「挑人」那张单子上的每一行（头像 + 名字 + @handle + 标）。名册还没读到（列不出
// 候选）时是空的 —— 那是要的：不知道房里已经有谁就不该让人挑。
function pickerRows(): string[] {
  return Array.from(document.querySelectorAll('.member-picker__row')).map((n) => n.textContent ?? '')
}
function pickerRowOf(text: string): HTMLElement {
  return Array.from(document.querySelectorAll<HTMLElement>('.member-picker__row')).find((n) =>
    n.textContent?.includes(text)
  )!
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
  server.failFor = new Set()
  server.attempts = []
  server.hold = null
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
    expect(document.querySelector('.member-picker')).toBeNull()
  })

  it('私密频道里的人不管频道也能拉人，但移不了人', async () => {
    await openRoster({ canManage: false, canInvite: true })
    expect(document.querySelector('.member-picker')).not.toBeNull()
    expect(document.querySelector('.roster__remove')).toBeNull()
  })

  it('「综合」里的人移不出去，只有 AI 队友能请进请出', async () => {
    await openRoster({ general: true })
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    const bobRow = rows.find((r) => r.textContent?.includes('Bob'))!
    const agent = rows.find((r) => r.textContent?.includes('cheese-t1'))!
    expect(bobRow.querySelector('.roster__remove')).toBeNull()
    expect(agent.querySelector('.roster__remove')).not.toBeNull()
    const items = pickerRows()
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
    const items = pickerRows()
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

  it('往外交出「管理者」时给的是一整个身份，不是一个名字', async () => {
    // 频道详情要画头像：名册出口必须带 handle/avatarId/agent，否则接的人只能写个名字。
    const utils = await openRoster()
    const calls = utils.emitted('manager') as unknown[][]
    const last = calls.at(-1)![0] as Record<string, unknown>
    expect(last).toEqual({ handle: 'alice', name: 'Alice', avatarId: null, agent: false })
  })

  it('批量请人：请不成的留在选中里、请成的退出去，再按一次只重试没成的', async () => {
    // 两个都还不在房间里的候选：Dave 会成，评审（cheese-a2）会失败。
    await openRoster()
    server.failFor.add('cheese-a2')
    await fireEvent.click(pickerRowOf('Dave'))
    await fireEvent.click(pickerRowOf('评审'))
    await settle()
    // 勾两个人，先看清 chip 上有这两位，还没发请求。
    expect(Array.from(document.querySelectorAll('.member-picker__chip')).map((n) => n.textContent)).toEqual([
      expect.stringContaining('Dave'),
      expect.stringContaining('评审'),
    ])

    await fireEvent.click(
      Array.from(document.querySelectorAll('.roster__add button')).find((b) => b.textContent?.trim() === '加入')!
    )
    await waitFor(() =>
      expect(Array.from(document.querySelectorAll('.roster__item')).some((r) => r.textContent?.includes('dave'))).toBe(
        true
      )
    )
    // 成的（Dave）从选中里退出去；没成的（评审）留在原地等再按一次。
    expect(Array.from(document.querySelectorAll('.member-picker__chip')).map((n) => n.textContent)).toEqual([
      expect.stringContaining('评审'),
    ])
    expect(document.querySelector('.roster__error')?.textContent).toContain('邀请失败')
    expect(server.attempts).toEqual(['t1:dave', 't1:cheese-a2'])

    // 再按一次：只把评审再请一遍，不再动已经坐在里面的 Dave。
    server.failFor.delete('cheese-a2')
    await fireEvent.click(
      Array.from(document.querySelectorAll('.roster__add button')).find((b) => b.textContent?.trim() === '加入')!
    )
    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('.roster__item')).some((r) => r.textContent?.includes('cheese-a2'))
      ).toBe(true)
    )
    expect(document.querySelectorAll('.member-picker__chip')).toHaveLength(0)
    // Dave 一共只请过一次：成的不重请。
    expect(server.attempts.filter((a) => a === 't1:dave')).toHaveLength(1)
    expect(server.attempts.filter((a) => a === 't1:cheese-a2')).toHaveLength(2)
  })

  it('名册第一次就没读回来：不知道房里已经有谁，先不许挑人', async () => {
    vi.mocked(listTopicMembers).mockImplementationOnce(() => Promise.reject(new Error('读不到名册')))
    await openRoster()
    // 名册没到手（读失败），loading 已经变成 false，但挑人这一件整个停用。
    const search = document.querySelector<HTMLInputElement>('.member-picker__search input')
    expect(search?.disabled).toBe(true)
    const add = Array.from(document.querySelectorAll<HTMLButtonElement>('.roster__add button')).find(
      (b) => b.textContent?.trim() === '加入'
    )!
    expect(add.disabled).toBe(true)
    // 点也不该发出任何请求。
    await fireEvent.click(pickerRowOf('Dave'))
    await settle()
    expect(server.attempts).toEqual([])
  })

  it('请人还没回来就切了频道：整批只进原来的频道，新频道的选择不被盖上', async () => {
    const utils = await openRoster()
    let release!: () => void
    server.hold = { promise: new Promise<void>((r) => (release = r)), release: () => release() }
    await fireEvent.click(pickerRowOf('Dave'))
    await fireEvent.click(pickerRowOf('评审'))
    await settle()
    await fireEvent.click(
      Array.from(document.querySelectorAll('.roster__add button')).find((b) => b.textContent?.trim() === '加入')!
    )
    await settle()
    // 请人卡在半路，切去另一个频道。
    await utils.rerender({
      props: { topicId: 't2', projectId: 'p1', projectMembers: PROJECT_MEMBERS, me: 'alice', canManage: true },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await settle()
    server.hold.release()
    await settle()
    // 两笔都进了 t1，一笔都没落到切过去的 t2。
    expect(server.attempts).toEqual(['t1:dave', 't1:cheese-a2'])
    expect(server.attempts.some((a) => a.startsWith('t2:'))).toBe(false)
    // 切了频道，旧频道挑的人不留到新频道上。
    expect(document.querySelectorAll('.member-picker__chip')).toHaveLength(0)
    // 旧频道那一下的报错也不该挂到新频道上（那批人是请进了 t1 的）。
    expect(document.querySelector('.roster__error')).toBeNull()
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
    const items = pickerRows()
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
    expect(row.textContent).toContain('新会话默认：沙箱 · 标准')
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
  // 名册上还没有他（他现在只出现在「添加」那张单子里）。
  const onRoster = () =>
    Array.from(document.querySelectorAll('.roster__item')).some((r) => r.textContent?.includes('dave'))
  expect(onRoster()).toBe(false)

  // 勾一个：勾了不该发请求，只是选中。
  await fireEvent.click(pickerRowOf('Dave'))
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

    // 「添加」那张单子上，名字下面那行 @handle 用的是 IdentityView 的 handleLine。
    const options = Array.from(document.querySelectorAll('.member-picker__row .identity-view__handle')).map((el) =>
      el.textContent?.trim()
    )
    expect(options).toContain('@reviewer')
    expect(options).not.toContain('@cheese-a2')
  })
})
