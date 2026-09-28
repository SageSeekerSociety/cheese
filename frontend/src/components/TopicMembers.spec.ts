import type { Component } from 'vue'
import type { ComputeChoice, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const machines = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    getTopicComputeProfile: (...args: unknown[]) => machines.get(...args),
    listTopicMembers: vi.fn(async () => ({
      data: [
        { id: '1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false, avatar_id: null },
        { id: '3', member_handle: 'bob', name: 'Bob', role: 'member', agent: false, avatar_id: null },
        { id: '4', member_handle: 'carol', name: 'Carol', role: 'member', agent: false, avatar_id: 77 },
        { id: '2', member_handle: 'cheese-t1', name: '芝士', role: 'member', agent: true, avatar_id: null },
      ],
      total: 4,
    })),
  }
})

import { setLocale } from '../i18n'

import TopicMembers from './TopicMembers.vue'

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

async function openRoster() {
  const utils = render(Roster, {
    props: { topicId: 't1', projectMembers: PROJECT_MEMBERS, me: 'alice' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await settle()
  await fireEvent.click(document.querySelector('.members-mini')!)
  await settle()
  return utils
}

const CLOUD: ComputeChoice = {
  name: '云端 · 标准配置',
  profile: 'cloud',
  device_id: null,
  cores: null,
  memory_mb: null,
  disk_gb: null,
}
const LAB: ComputeChoice = { ...CLOUD, name: '自有设备 · 自动选择', profile: 'device', device_id: null }

function roomMachines(overrides: Partial<TopicComputeProfile> = {}): TopicComputeProfile {
  return {
    choice: CLOUD,
    project_default: CLOUD,
    favorites: [],
    current: 'cloud',
    device_id: null,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true }],
    sessions: [],
    profiles: [],
    visibility: { options: [], effective: null, machine_access: false, notice: '能操作这台机器上的服务和其他房间' },
    ...overrides,
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  document.body.innerHTML = ''
  machines.get.mockReset().mockResolvedValue(roomMachines())
})

describe('成员名册', () => {
  it('队友和人一样能被移出，但房间没有「换队友」这种开关', async () => {
    await openRoster()
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    expect(rows.length).toBe(4)

    const agentRow = rows.find((r) => r.textContent?.includes('cheese-t1'))!
    const humanRow = rows.find((r) => r.textContent?.includes('alice'))!
    expect(agentRow.querySelector('.roster__remove')).not.toBeNull()
    expect(agentRow.querySelector('.roster__role--btn')).toBeNull()
    expect(humanRow.querySelector('.roster__role')).not.toBeNull()
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
    const face = faceOf('carol')
    expect(face.tagName).toBe('IMG')
    expect(face.getAttribute('src')).toContain('/avatars/77')
  })
})

describe('外部成员在房间里', () => {
  it('名册上团队以外的人挂「外部」，团队里的人不挂', async () => {
    await openRoster()
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    const carol = rows.find((r) => r.textContent?.includes('@carol'))!
    const bob = rows.find((r) => r.textContent?.includes('@bob'))!
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

describe('名册上 AI 队友的工作电脑', () => {
  it('开工了的队友写它现在那台，自有设备后面跟着「能访问整台机器」', async () => {
    machines.get.mockResolvedValue(
      roomMachines({
        visibility: {
          options: [],
          effective: 'host',
          machine_access: true,
          notice: '能操作这台机器上的服务和其他房间',
        },
        sessions: [
          {
            id: 's1',
            agent_handle: 'cheese-t1',
            harness: 'claude-code',
            choice: LAB,
            lease: { device_id: 'lab', generation: 1, status: 'ready', online: true },
            machine_access: true,
          },
        ],
      })
    )
    await openRoster()
    const line = agentRow().querySelector('[data-testid="agent-machine"]')!
    // 「自动选一台」开工后已经落在某一台上：写那一台的名字。
    expect(line.textContent).toContain('工作电脑：实验室工作站')
    expect(line.textContent).toContain('能访问整台机器')
    expect(line.textContent).toContain('更换')
    // 已经在干活的那一台不跟着项目走，不挂「项目默认」。
    expect(line.textContent).not.toContain('项目默认')
    const humans = Array.from(document.querySelectorAll('.roster__item')).filter((r) => r !== agentRow())
    expect(humans.every((r) => !r.textContent?.includes('工作电脑'))).toBe(true)
  })

  it('云端上的队友不挂整台机器的提醒', async () => {
    machines.get.mockResolvedValue(
      roomMachines({
        sessions: [
          {
            id: 's1',
            agent_handle: 'cheese-t1',
            harness: 'claude-code',
            choice: CLOUD,
            lease: null,
            machine_access: false,
          },
        ],
      })
    )
    await openRoster()
    const line = agentRow().querySelector('[data-testid="agent-machine"]')!
    expect(line.textContent).toContain('工作电脑：云端 · 标准配置')
    expect(line.textContent).not.toContain('能访问整台机器')
    expect(line.textContent).not.toContain('项目默认')
  })

  it('还没开工的队友写开工时会用哪台，跟着项目默认时标出来', async () => {
    await openRoster()
    const line = agentRow().querySelector('[data-testid="agent-machine"]')!
    expect(line.textContent).toContain('还没开工 · 将用：云端 · 标准配置')
    expect(line.textContent).toContain('项目默认')
    expect(line.textContent).toContain('改')
    expect(line.textContent).not.toContain('更换')
  })

  it('名册下面写着之后邀请的 AI 队友用哪台', async () => {
    machines.get.mockResolvedValue(roomMachines({ choice: { ...LAB, name: '实验室工作站', device_id: 'lab' } }))
    await openRoster()
    const future = document.querySelector('[data-testid="future-machine"]')!
    expect(future.textContent).toContain('之后邀请的 AI 队友用：实验室工作站')
    expect(future.textContent).not.toContain('项目默认')
    expect(future.textContent).toContain('改')
  })

  it('更换打开的是给这一位队友换电脑的对话框', async () => {
    machines.get.mockResolvedValue(
      roomMachines({
        sessions: [
          {
            id: 's1',
            agent_handle: 'cheese-t1',
            harness: 'claude-code',
            choice: CLOUD,
            lease: null,
            machine_access: false,
          },
        ],
      })
    )
    await openRoster()
    const change = Array.from(agentRow().querySelectorAll('button')).find((b) => b.textContent?.trim() === '更换')!
    await fireEvent.click(change)
    await settle()
    expect(document.body.textContent).toContain('给 芝士 换一台工作电脑')
  })

  it('有队友能访问整台机器时告诉页头，名册合着也看得见', async () => {
    machines.get.mockResolvedValue(
      roomMachines({
        visibility: {
          options: [],
          effective: 'host',
          machine_access: true,
          notice: '能操作这台机器上的服务和其他房间',
        },
      })
    )
    const { emitted } = render(Roster, {
      props: { topicId: 't1', projectMembers: PROJECT_MEMBERS, me: 'alice' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await settle()
    const notices = emitted()['machine-access'] as [string | null][]
    expect(notices.at(-1)).toEqual(['能操作这台机器上的服务和其他房间'])
  })
})
