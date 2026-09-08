// 名册在成员到达之前画什么。
//
// 这里以前是一行「加载中…」——一句话既说不出这份名册有多长，也不长成它的样子，
// 名单一到整块面板就变一次高。名册的形状是已知的（一张脸 + 两行字），所以画它。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listTopicMembers = vi.fn()

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listTopicMembers: (...a: unknown[]) => listTopicMembers(...a),
    getTopicAgent: vi.fn(async () => ({ display_name: '芝士', inherited: true, instance_id: 'a1' })),
    listProjectAgents: vi.fn(async () => ({ data: [] })),
  }
})

import TopicMembers from './TopicMembers.vue'

const Roster = TopicMembers as unknown as Component

function pending<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
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
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  listTopicMembers.mockReset()
  document.body.innerHTML = ''
})

async function openRoster() {
  render(Roster, {
    props: { topicId: 't1', projectId: 'p1', projectMembers: [], me: 'alice' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await settle()
  await fireEvent.click(document.querySelector('.members-mini')!)
  await settle()
}

describe('名单还在路上的名册', () => {
  it('画的是名册行的形状：一张脸 + 两行字', async () => {
    const gate = pending<{ data: unknown[]; total: number }>()
    listTopicMembers.mockReturnValue(gate.promise)
    await openRoster()

    const skeleton = document.querySelector('.roster [role="status"][aria-busy="true"]')
    expect(skeleton, '名册的形状是已知的，等待期间就该画出来').not.toBeNull()
    expect(skeleton!.querySelector('.skel__bone--face'), '没有脸的位置，名单一到每一行都要往右挪').not.toBeNull()
    expect(document.querySelector('.roster__item'), '名单还没到，真的行不该在').toBeNull()

    gate.resolve({
      data: [{ id: '1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false, avatar_id: null }],
      total: 1,
    })
    await settle()
  })

  it('名单一到，骨架就走干净', async () => {
    const gate = pending<{ data: unknown[]; total: number }>()
    listTopicMembers.mockReturnValue(gate.promise)
    await openRoster()
    expect(document.querySelector('.roster [role="status"][aria-busy="true"]')).not.toBeNull()

    gate.resolve({
      data: [
        { id: '1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false, avatar_id: null },
        { id: '2', member_handle: 'bob', name: 'Bob', role: 'member', agent: false, avatar_id: null },
      ],
      total: 2,
    })

    await waitFor(() => expect(document.querySelectorAll('.roster__item').length).toBe(2))
    expect(document.querySelector('.roster [role="status"][aria-busy="true"]')).toBeNull()
  })
})
