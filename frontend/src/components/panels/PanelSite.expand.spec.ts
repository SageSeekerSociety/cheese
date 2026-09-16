// 点开现场的一行看到的是参数原文：一行为了能扫而重写过、剪短过，摊开的人要的
// 正是被剪掉的那截。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
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

const HEREDOC = "cat > report.md <<'EOF'\n# 标题\n正文\nEOF"

function event(meta: Record<string, unknown>): Block {
  return {
    id: 'b1',
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'ai',
    author: 'cheese-t1',
    content: '',
    reply_to: null,
    refs: [],
    turn_id: 'turn-a',
    meta,
    created_at: '2026-09-16T10:00:00Z',
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

async function openSite(blocks: Block[]) {
  getTranscript.mockResolvedValue({ data: blocks, total: blocks.length })
  const { container } = render(Site, {
    props: { topic, active: true },
    global: { plugins: [vuetify] },
  })
  await waitFor(() => expect(container.querySelector('.site-act')).not.toBeNull())
  return container
}

describe('点开现场的一行', () => {
  it('摊开的是参数原文，不是那一行的省略版', async () => {
    const container = await openSite([event({ tool: 'bash', as_tool: 'Write', arg: 'report.md', detail: HEREDOC })])
    const arg = container.querySelector<HTMLElement>('[data-testid="site-act-arg"]')!
    expect(arg.textContent).toBe('report.md')

    await fireEvent.click(arg)
    expect(arg.textContent).toBe(HEREDOC)

    await fireEvent.click(arg)
    expect(arg.textContent).toBe('report.md')
  })

  it('没有第二份时摊开的仍是这一行本身', async () => {
    const container = await openSite([event({ tool: 'bash', arg: 'make test' })])
    const arg = container.querySelector<HTMLElement>('[data-testid="site-act-arg"]')!

    await fireEvent.click(arg)
    expect(arg.textContent).toBe('make test')
  })
})
