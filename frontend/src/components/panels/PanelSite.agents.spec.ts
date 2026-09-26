// 一个房间里有几个队友在干活时，现场可以只看其中一个：顶上一排「全部」加每个队友
// 的名字，状态条说的是选中的那一个（「全部」下是此刻在动的那一个）。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTranscript = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getTranscript: (...a: unknown[]) => getTranscript(...a),
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

const NAMES = { 'cheese-a1': '芝士', 'cheese-b2': '小苔' }

function step(id: string, author: string, turn: string, at: string, arg: string, extra: Partial<Block> = {}): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'participant',
    author,
    content: '',
    reply_to: null,
    refs: [],
    turn_id: turn,
    meta: { tool: 'Bash', arg },
    created_at: at,
    ...extra,
  } as unknown as Block
}

const TWO = [
  step('1', 'cheese-a1', 'turn-a', '2026-09-25T10:00:00Z', 'ls'),
  step('2', 'cheese-b2', 'turn-b', '2026-09-25T10:01:00Z', 'make docs'),
  step('3', 'cheese-a1', 'turn-a', '2026-09-25T10:02:00Z', 'pytest -q'),
  {
    ...step('4', 'system', 'turn-a', '2026-09-25T10:02:30Z', ''),
    author_type: 'platform',
    content: '本轮失败',
    meta: { event_type: 'turn_failed', severity: 'error', who: 'human' },
  } as unknown as Block,
]

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  getTranscript.mockReset()
})

async function openSite(blocks: Block[], props: Record<string, unknown> = {}) {
  getTranscript.mockResolvedValue({ data: blocks, total: blocks.length })
  const view = render(Site, {
    props: { topic, active: true, memberNames: NAMES, ...props },
    global: { plugins: [vuetify] },
  })
  await waitFor(() => expect(view.container.querySelector('.site-act')).not.toBeNull())
  return view
}

function args(container: Element): string[] {
  return Array.from(container.querySelectorAll('[data-testid="site-act-arg"]')).map((e) => e.textContent?.trim() ?? '')
}

function status(container: Element): string {
  return container.querySelector('[data-testid="site-status"]')?.textContent?.replace(/\s+/g, ' ') ?? ''
}

describe('现场按队友看', () => {
  it('不止一个队友时出现「全部」加每个队友的名字', async () => {
    const view = await openSite(TWO)

    const tabs = view.getAllByRole('tab').map((t) => t.textContent?.trim())
    expect(tabs).toEqual(['全部', '芝士', '小苔'])
    expect(view.getByRole('tab', { name: '全部' }).getAttribute('aria-selected')).toBe('true')
    expect(args(view.container)).toEqual(['ls', 'make docs', 'pytest -q'])
  })

  it('选一个队友只看它做的；平台自己的话只在「全部」里', async () => {
    const view = await openSite(TWO)

    await fireEvent.click(view.getByRole('tab', { name: '芝士' }))
    expect(args(view.container)).toEqual(['ls', 'pytest -q'])
    expect(view.container.textContent).not.toContain('本轮失败')

    await fireEvent.click(view.getByRole('tab', { name: '小苔' }))
    expect(args(view.container)).toEqual(['make docs'])

    await fireEvent.click(view.getByRole('tab', { name: '全部' }))
    expect(view.container.textContent).toContain('本轮失败')
  })

  it('只有一个队友：没有这一排', async () => {
    const view = await openSite([TWO[0], TWO[2]])

    expect(view.queryAllByRole('tab')).toHaveLength(0)
  })

  it('状态条：「全部」下说此刻在动的那个队友，选中别的队友时说它闲着', async () => {
    const running = { 'turn-b': Date.parse('2026-09-25T10:00:50Z') }
    const view = await openSite(TWO.slice(0, 3), { working: true, runningTurns: running })

    expect(status(view.container)).toContain('小苔')
    expect(status(view.container)).toContain('正在执行命令')

    await fireEvent.click(view.getByRole('tab', { name: '芝士' }))
    expect(status(view.container)).toContain('芝士')
    expect(status(view.container)).toContain('空闲')
  })
})
