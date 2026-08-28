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
import { beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [
        { id: '1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
        { id: '2', member_handle: 'cheese-t1', name: '芝士', role: 'member', agent: true },
      ],
      total: 2,
    }),
    getTopicAgent: vi.fn().mockResolvedValue({ display_name: '芝士', inherited: true, instance_id: 'a1' }),
    listProjectAgents: vi.fn().mockResolvedValue({ data: [{ id: 'a2', display_name: '另一个', is_default: false }] }),
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

async function openRoster() {
  const utils = render(Roster, {
    props: { topicId: 't1', projectId: 'p1', projectMembers: [], me: 'alice' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
  await fireEvent.click(document.querySelector('.members-mini')!)
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
  return utils
}

describe('成员名册', () => {
  it('芝士那一行上有换队友，人那一行上没有', async () => {
    await openRoster()
    const rows = Array.from(document.querySelectorAll('.roster__item'))
    expect(rows.length).toBe(2)

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
