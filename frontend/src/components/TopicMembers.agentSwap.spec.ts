// 换 AI 队友长在成员名册里芝士那一行上。
//
// 它以前在输入区的动作行里：那一行是「这条消息」的动作（发图、发送），而换队友
// 是「这个房间里有谁」——换完这个话题的会话要重来，是个有代价、几乎只做一次的
// 动作。芝士本来就在这份名册里（带 Agent 标），所以换它和换一个人的角色是同一
// 类动作，长在同一个位置上。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// 名册那一行上芝士叫什么，由后端解析成「这个房间现在交给的那个队友」。换人之后
// 再拉一次名册拿到的就是新名字 —— 这几个测试正是照着这条路走的。
let agentName = '芝士'
const setTopicAgent = vi.fn(async () => {
  agentName = '另一个'
  return { display_name: agentName, inherited: false, instance_id: 'a2' }
})

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listTopicMembers: vi.fn(async () => ({
      data: [
        { id: '1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false, avatar_id: null },
        { id: '3', member_handle: 'bob', name: 'Bob', role: 'member', agent: false, avatar_id: null },
        { id: '4', member_handle: 'carol', name: 'Carol', role: 'member', agent: false, avatar_id: 77 },
        { id: '2', member_handle: 'cheese-t1', name: agentName, role: 'member', agent: true, avatar_id: null },
      ],
      total: 4,
    })),
    getTopicAgent: vi.fn(async () => ({ display_name: agentName, inherited: true, instance_id: 'a1' })),
    listProjectAgents: vi.fn().mockResolvedValue({ data: [{ id: 'a2', display_name: '另一个', is_default: false }] }),
    setTopicAgent: (...args: unknown[]) => setTopicAgent(...(args as [])),
  }
})

import TopicMembers from './TopicMembers.vue'

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
    props: { topicId: 't1', projectId: 'p1', projectMembers: [], me: 'alice' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await settle()
  await fireEvent.click(document.querySelector('.members-mini')!)
  await settle()
  return utils
}

function agentRow(): Element {
  return Array.from(document.querySelectorAll('.roster__item')).find((r) => r.textContent?.includes('cheese-t1'))!
}

beforeEach(() => {
  agentName = '芝士'
  document.body.innerHTML = ''
})

describe('成员名册', () => {
  it('芝士那一行上有换队友，人那一行上没有', async () => {
    await openRoster()
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    expect(rows.length).toBe(4)

    const agentRow = rows.find((r) => r.textContent?.includes('cheese-t1'))!
    const humanRow = rows.find((r) => r.textContent?.includes('alice'))!
    expect(agentRow.querySelector('.ap-swap'), '芝士那一行没有换队友').toBeTruthy()
    expect(humanRow.querySelector('.ap-swap'), '人那一行不该有换队友').toBeNull()
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

describe('换 AI 队友', () => {
  it('换完，名册那一行当场写的是新队友的名字', async () => {
    const { emitted } = await openRoster()
    expect(agentRow().querySelector('.roster__name')?.textContent?.trim()).toBe('芝士')

    await fireEvent.click(agentRow().querySelector('.ap-swap')!)
    await settle()
    // 菜单里那一项：「另一个」。
    const option = Array.from(document.querySelectorAll('.v-list-item-title')).find(
      (n) => n.textContent?.trim() === '另一个'
    )!
    await fireEvent.click(option.closest('.v-list-item')!)
    await settle()

    expect(setTopicAgent).toHaveBeenCalledWith('t1', 'a2')
    // 名册不重拉的话，这里留着的是上一个队友的名字 —— 和「换人没生效」一模一样。
    expect(agentRow().querySelector('.roster__name')?.textContent?.trim()).toBe('另一个')
    // 对话栏显示的 AI 名字也来自名册，而它够不着这个组件：得往上说一声。
    expect(emitted()['agent-swapped']).toBeTruthy()
  })

  it('顶上那句「N 人 + …」跟的也是当前队友，不是写死的「芝士」', async () => {
    await openRoster()
    expect(document.querySelector('.roster__count')?.textContent?.trim()).toBe('3 人 + 芝士')

    await fireEvent.click(agentRow().querySelector('.ap-swap')!)
    await settle()
    const option = Array.from(document.querySelectorAll('.v-list-item-title')).find(
      (n) => n.textContent?.trim() === '另一个'
    )!
    await fireEvent.click(option.closest('.v-list-item')!)
    await settle()

    expect(document.querySelector('.roster__count')?.textContent?.trim()).toBe('3 人 + 另一个')
  })
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
