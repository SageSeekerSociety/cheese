// 摊开的那一步下面能看到它打印了什么：默认收着，点了才去取；没打印东西的步骤
// 不给这个入口。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTranscript = vi.fn()
const getStepOutput = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getTranscript: (...a: unknown[]) => getTranscript(...a),
    getStepOutput: (...a: unknown[]) => getStepOutput(...a),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false, tasks: {} }),
  }
})

import PanelSite from './PanelSite.vue'

import { setLocale } from '@/i18n'

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

function step(id: string, arg: string, meta: Record<string, unknown> = {}): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-t1',
    content: '',
    reply_to: null,
    refs: [],
    turn_id: 'turn-a',
    meta: { tool: 'Bash', arg, ...meta },
    created_at: '2026-09-25T10:00:00Z',
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  getTranscript.mockReset()
  getStepOutput.mockReset()
})

async function openSite(blocks: Block[]) {
  getTranscript.mockResolvedValue({ data: blocks, total: blocks.length })
  const view = render(Site, { props: { topic, active: true }, global: { plugins: [vuetify] } })
  await waitFor(() => expect(view.container.querySelector('.site-act')).not.toBeNull())
  return view
}

describe('现场一步的输出', () => {
  it('摊开那一步才有「输出」入口，点了才去取，取回来原样显示', async () => {
    getStepOutput.mockResolvedValue({ output: '42 passed\n', bytes: 10 })
    const view = await openSite([step('s1', 'pytest -q', { output_bytes: 10 })])

    expect(view.queryByText(/输出（/)).toBeNull()
    await fireEvent.click(view.getByTestId('site-act-arg'))
    const toggle = view.getByText('输出（10 B）')
    expect(getStepOutput).not.toHaveBeenCalled()

    await fireEvent.click(toggle)
    await waitFor(() => expect(view.getByTestId('site-step-output').textContent).toContain('42 passed'))
    expect(getStepOutput).toHaveBeenCalledWith('t1', 's1')
    expect(view.queryByText(/仅显示最后/)).toBeNull()
  })

  it('后端只留了末尾一截时，说只看到了末尾', async () => {
    getStepOutput.mockResolvedValue({ output: 'tail', bytes: 50_000 })
    const view = await openSite([step('s1', 'make build', { output_bytes: 50_000 })])

    await fireEvent.click(view.getByTestId('site-act-arg'))
    await fireEvent.click(view.getByText('输出（49 KB）'))

    await waitFor(() => expect(view.getByText('仅显示最后 4 B')).toBeTruthy())
  })

  it('没打印东西的一步，摊开也没有输出入口', async () => {
    const view = await openSite([step('s1', 'touch a')])

    await fireEvent.click(view.getByTestId('site-act-arg'))

    expect(view.queryByText(/输出（/)).toBeNull()
  })
})
