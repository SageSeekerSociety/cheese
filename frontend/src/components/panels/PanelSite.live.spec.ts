// 现场不等人重新打开：socket 上来的一行直接接上，已有的一行变了就原地换掉，和
// 打开时读回来的那一页合成一份，一行不重、一行不闪。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
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

function step(
  id: string,
  at: string,
  arg: string,
  extra: Partial<Block> = {},
  meta: Record<string, unknown> = {}
): Block {
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
    meta: { tool: 'bash', arg, ...meta },
    created_at: at,
    ...extra,
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getTranscript.mockReset()
})

// 现场的这一行是对话栏转过来的：测试站在 WorkPanel 的位置上，拿着它的 ref 往里递。
async function openSite(blocks: Block[]) {
  let resolve: (v: unknown) => void = () => {}
  getTranscript.mockReturnValue(new Promise((r) => (resolve = r)))
  const site = ref<{ receive: (b: Block) => void } | null>(null)
  const Host = defineComponent({
    setup: () => () => h(PanelSite as unknown as Component, { ref: site, topic, active: true }),
  })
  const view = render(Host, { global: { plugins: [vuetify] } })
  return {
    ...view,
    push: (b: Block) => site.value?.receive(b),
    answer: async () => {
      resolve({ data: blocks, total: blocks.length })
      await waitFor(() => expect(view.container.querySelector('.site-log')).not.toBeNull())
    },
  }
}

function args(container: Element): string[] {
  return Array.from(container.querySelectorAll('[data-testid="site-act-arg"]')).map((e) => e.textContent?.trim() ?? '')
}

describe('现场实时接上', () => {
  it('socket 上来的一步接在末尾，不用重新打开', async () => {
    const site = await openSite([step('1', '2026-09-25T10:00:00Z', 'ls')])
    await site.answer()

    site.push(step('2', '2026-09-25T10:00:05Z', 'make test'))
    await nextTick()

    expect(args(site.container)).toEqual(['ls', 'make test'])
    expect(getTranscript).toHaveBeenCalledTimes(1)
  })

  it('读回来的那一页里已经有的一行，socket 再送一遍也只出现一次', async () => {
    const site = await openSite([
      step('1', '2026-09-25T10:00:00Z', 'ls'),
      step('2', '2026-09-25T10:00:05Z', 'make test'),
    ])
    // 这一行在读的请求还在路上时就到了，读回来的那一页里也有它。
    site.push(step('2', '2026-09-25T10:00:05Z', 'make test'))
    // 这一行比那一页新：读回来之后它还在。
    site.push(step('3', '2026-09-25T10:00:09Z', 'pytest'))
    await site.answer()

    expect(args(site.container)).toEqual(['ls', 'make test', 'pytest'])
  })

  it('一步挂了：那一行原地变红，不多出一行', async () => {
    const site = await openSite([step('1', '2026-09-25T10:00:00Z', 'pandoc a.md')])
    await site.answer()

    site.push(step('1', '2026-09-25T10:00:00Z', 'pandoc a.md', {}, { failed: true, error: 'command not found' }))
    await nextTick()

    expect(site.container.querySelectorAll('.site-act')).toHaveLength(1)
    expect(site.container.querySelector('.site-act--failed')).not.toBeNull()
    expect(site.getByTestId('site-act-error').textContent).toContain('command not found')
  })

  it('分身卡上的一步、别的话题的一步，都不进这一栏', async () => {
    const site = await openSite([step('1', '2026-09-25T10:00:00Z', 'ls')])
    await site.answer()

    site.push(step('2', '2026-09-25T10:00:05Z', 'on a card', { task_id: 'card-1' }))
    site.push(step('3', '2026-09-25T10:00:06Z', 'elsewhere', { topic_id: 't2' }))
    await nextTick()

    expect(args(site.container)).toEqual(['ls'])
  })
})
