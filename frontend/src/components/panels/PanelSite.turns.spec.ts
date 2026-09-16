// 现场是一轮一轮的：同一轮的步骤收在一组里，组头说这一轮几步、多久，芝士自己
// 说的话按话显示而不是按一次操作显示。
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

function event(id: string, turn: string | null, at: string, meta: Record<string, unknown>, content = ''): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'ai',
    author: 'cheese-t1',
    content,
    reply_to: null,
    refs: [],
    turn_id: turn,
    meta,
    created_at: at,
  } as unknown as Block
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

async function openSite(blocks: Block[], props: Record<string, unknown> = {}) {
  getTranscript.mockResolvedValue({ data: blocks, total: blocks.length })
  const { container } = render(Site, {
    props: { topic, active: true, ...props },
    global: { plugins: [vuetify] },
  })
  await waitFor(() => expect(container.querySelector('.turn')).not.toBeNull())
  return container
}

describe('现场按轮组织', () => {
  it('同一轮的步骤在一组里，组头说这一轮几步、多久', async () => {
    const container = await openSite([
      event('1', 'turn-a', '2026-09-15T20:28:00Z', { tool: 'write', arg: 'build.py' }),
      event('2', 'turn-a', '2026-09-15T20:28:30Z', { tool: 'bash', arg: 'python3 build.py' }),
      event('3', 'turn-a', '2026-09-15T20:29:04Z', { tool: 'read', arg: 'output/intro.docx' }),
    ])

    expect(container.querySelectorAll('.turn')).toHaveLength(1)
    expect(container.querySelectorAll('.site-act')).toHaveLength(3)
    const head = container.querySelector('.turn__head')?.textContent ?? ''
    expect(head).toContain('3 步')
    expect(head).toContain('1 分 04 秒')
  })

  it('换了一轮就换一组', async () => {
    const container = await openSite([
      event('1', 'turn-a', '2026-09-15T20:28:00Z', { tool: 'bash', arg: 'ls' }),
      event('2', 'turn-b', '2026-09-15T20:31:00Z', { tool: 'bash', arg: 'ls' }),
    ])

    expect(container.querySelectorAll('.turn')).toHaveLength(2)
  })

  it('芝士说的话按话显示，不占一步', async () => {
    const container = await openSite([
      event('1', 'turn-a', '2026-09-15T20:28:00Z', { tool: 'bash', arg: 'ls' }),
      event('2', 'turn-a', '2026-09-15T20:29:00Z', { progress: true }, '文档已生成并展示。'),
    ])

    expect(container.querySelector('.site-msg')?.textContent).toContain('文档已生成并展示。')
    expect(container.querySelectorAll('.site-act')).toHaveLength(1)
    expect(container.querySelector('.turn__head')?.textContent).toContain('1 步')
  })

  it('房间里有活在跑时，最后一组说进行中', async () => {
    const container = await openSite(
      [
        event('1', 'turn-a', '2026-09-15T20:28:00Z', { tool: 'bash', arg: 'ls' }),
        event('2', 'turn-b', '2026-09-15T20:31:00Z', { tool: 'bash', arg: 'ls' }),
      ],
      { working: true }
    )

    const heads = Array.from(container.querySelectorAll('.turn__head')).map((h) => h.textContent ?? '')
    expect(heads[0]).not.toContain('进行中')
    expect(heads[1]).toContain('进行中')
  })

  it('没有活在跑时谁都不说进行中', async () => {
    const container = await openSite([event('1', 'turn-a', '2026-09-15T20:28:00Z', { tool: 'bash', arg: 'ls' })])

    expect(container.textContent).not.toContain('进行中')
  })
})
