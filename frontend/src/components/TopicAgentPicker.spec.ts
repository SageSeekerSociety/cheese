// 「换队友」这个菜单列的是**还能接活**的队友。
//
// 停用一个队友的意思就是「别再把新活交给它」，所以它必须从这里消失 —— 一个接口
// 通了、挑选器却照样选得到的停用，等于没停。而它在别的房间里还在正常干活，那些
// 房间不受影响：这里只管「接下来交给谁」。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectAgents = vi.fn()
const getTopicAgent = vi.fn()
const setTopicAgent = vi.fn()

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
    getTopicAgent: (...a: unknown[]) => getTopicAgent(...a),
    setTopicAgent: (...a: unknown[]) => setTopicAgent(...a),
  }
})

import TopicAgentPicker from './TopicAgentPicker.vue'

const Picker = TopicAgentPicker as unknown as Component

function projectAgent(over: Record<string, unknown> = {}) {
  return {
    id: 'a2',
    project_id: 'p1',
    handle: 'reviewer',
    type_name: null,
    display_name: '评审',
    is_default: false,
    configured: true,
    is_active: true,
    ...over,
  }
}

// Vuetify 的浮层要这几样浏览器 API（happy-dom 没有），同 TopicMembers.agentSwap.spec。
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

beforeEach(() => {
  listProjectAgents.mockReset()
  setTopicAgent.mockReset()
  getTopicAgent.mockReset().mockResolvedValue({ display_name: '芝士', inherited: true, instance_id: 'a1' })
})

async function openMenu() {
  render(Picker, {
    props: { topicId: 't1', projectId: 'p1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
  await fireEvent.click(document.querySelector('.ap-swap')!)
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('换队友', () => {
  it('已停用的队友选不到', async () => {
    listProjectAgents.mockResolvedValue({
      data: [
        projectAgent({ id: 'a2', display_name: '评审' }),
        projectAgent({ id: 'a3', display_name: '退役的', is_active: false }),
      ],
      total: 2,
    })
    await openMenu()

    expect(document.body.textContent).toContain('评审')
    expect(document.body.textContent).not.toContain('退役的')
  })

  it('全都停用了就只剩当前这一个，菜单说清楚没有别人可选', async () => {
    listProjectAgents.mockResolvedValue({
      data: [projectAgent({ id: 'a3', display_name: '退役的', is_active: false })],
      total: 1,
    })
    await openMenu()

    expect(document.body.textContent).toContain('这个项目只有一个队友')
  })
})
