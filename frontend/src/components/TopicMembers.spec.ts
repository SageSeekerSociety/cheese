import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listTopicMembers: vi.fn(async () => ({
      data: [
        { id: '1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false, avatar_id: null },
        { id: '3', member_handle: 'bob', name: 'Bob', role: 'member', agent: false, avatar_id: null },
        { id: '4', member_handle: 'carol', name: 'Carol', role: 'member', agent: false, avatar_id: 77 },
        { id: '2', member_handle: 'cheese-t1', name: '芝士', role: 'member', agent: true, avatar_id: null },
      ],
      total: 4,
    })),
    // 项目里还有一个没进这个房间的队友：请它进来和请一个人是同一个「添加」列表。
    listProjectAgents: vi.fn(async () => ({
      data: [
        {
          id: 'a1',
          handle: 'cheese',
          seat_handle: 'cheese-t1',
          display_name: '芝士',
          is_default: true,
          is_active: true,
        },
        {
          id: 'a2',
          handle: 'cheese-pi',
          seat_handle: 'cheese-a2',
          display_name: '评审',
          is_default: false,
          is_active: true,
        },
        {
          id: 'a3',
          handle: 'old',
          seat_handle: 'cheese-a3',
          display_name: '退休',
          is_default: false,
          is_active: false,
        },
      ],
      total: 3,
    })),
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

beforeEach(() => {
  document.body.innerHTML = ''
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
    expect(document.body.textContent).not.toContain('换')
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
  // 头像挂在旁边，读起来像「几个人，另外还有个它」。
  await openRoster()
  expect(document.querySelector('.members-mini__count')!.textContent).toBe('4')
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
