// 现场：芝士干活的实况。
//
// 两件事在这一份里：记录还在路上时画的是那条流的形状（一条条「点 + 两行」的动作，
// 不是一个居中的转圈），以及记录到了之后 AI 那几行头像上的字——它必须跟着这个房间
// 现在的那个队友走，和它右边写的名字同一个来源，否则换过队友的房间里头像和名字对
// 不上，看起来就是「换人没生效」。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTranscript = vi.fn()
const getTerminal = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getTranscript: (...a: unknown[]) => getTranscript(...a),
    getTerminal: (...a: unknown[]) => getTerminal(...a),
  }
})

import PanelSite from './PanelSite.vue'

const Site = PanelSite as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '话题',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

function aiSaid(id: string, content: string): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'ai',
    author: 'cheese-t1',
    content,
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:10:00Z',
  } as unknown as Block
}

/** 一次拿得住的请求：先挂着，等测试自己决定什么时候让记录到达。 */
function pending<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getTranscript.mockReset()
  getTerminal.mockReset()
  getTerminal.mockResolvedValue({ available: false })
})

function open(memberNames: Record<string, string> = { 'cheese-t1': '芝士' }) {
  return render(Site, {
    props: { topic, active: true, memberNames },
    global: { plugins: [vuetify] },
  })
}

describe('现场还在路上的时候', () => {
  it('画的是这条流的形状，不是一个转圈', async () => {
    const gate = pending<{ data: Block[]; total: number }>()
    getTranscript.mockReturnValue(gate.promise)
    const { container } = open()

    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())
    expect(container.querySelector('.v-progress-circular'), '现场的形状是已知的，不该用转圈').toBeNull()
    const drawn = container.querySelectorAll('.skel > div').length
    expect(drawn, '一条只画一行等于说现场只发生了一件事').toBeGreaterThan(1)

    gate.resolve({ data: [aiSaid('a', '看过了')], total: 1 })
    await waitFor(() => expect(container.querySelector('.site-msg')).not.toBeNull())
  })

  it('记录到了，骨架就走干净', async () => {
    const gate = pending<{ data: Block[]; total: number }>()
    getTranscript.mockReturnValue(gate.promise)
    const { container } = open()
    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())

    gate.resolve({ data: [aiSaid('a', '看过了')], total: 1 })
    await waitFor(() => expect(container.textContent).toContain('看过了'))
    expect(container.querySelector('[role="status"][aria-busy="true"]'), '真的记录来了，骨架不能还在').toBeNull()
  })
})

describe('AI 那几行的头像', () => {
  it('头像上的字和它旁边的名字是同一个队友', async () => {
    getTranscript.mockResolvedValue({ data: [aiSaid('a', '看过了')], total: 1 })
    const { container } = open({ 'cheese-t1': '评审' })

    await waitFor(() => expect(container.querySelector('.site-msg')).not.toBeNull())
    expect(container.querySelector('.site-msg__name')?.textContent?.trim()).toBe('评审')
    // 写死的话这里会是「芝」——头像和名字当场对不上。
    expect(container.querySelector('.site-msg .cheese-avatar')?.textContent?.trim()).toBe('评')
  })
})
