// 现场里芝士说的那一段是给人读的话，不是终端回显：粗体、列表、代码块要和对话栏
// 一个样子，`<@handle>` `<&path>` 要变成认得出的 chip 并且点得动。
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

// 没有显式发布的输出就是这么落库的（meta.progress），不是一步操作。
function say(id: string, content: string, taskId: string | null = null): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-t1',
    content,
    reply_to: null,
    refs: [],
    turn_id: 'turn-a',
    task_id: taskId,
    meta: { progress: true },
    created_at: '2026-09-16T10:00:00Z',
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getTranscript.mockReset()
})

async function openSite(blocks: Block[], props: Record<string, unknown> = {}) {
  getTranscript.mockResolvedValue({ data: blocks, total: blocks.length })
  const utils = render(Site, {
    props: { topic, active: true, ...props },
    global: { plugins: [vuetify] },
  })
  await waitFor(() => expect(utils.container.querySelector('.site-msg__body')).not.toBeNull())
  return utils
}

describe('现场里芝士说的话', () => {
  it('按 markdown 渲染，星号不留字面', async () => {
    const { container } = await openSite([say('1', '做完的：\n- **可点原型** `demo.html` 已摆进房间')])

    const body = container.querySelector('.site-msg__body')!
    expect(body.querySelector('strong')?.textContent).toBe('可点原型')
    expect(body.querySelector('code')?.textContent).toBe('demo.html')
    expect(body.querySelector('li')).not.toBeNull()
    expect(body.textContent).not.toContain('**')
  })

  it('代码块成一个块，不是一段等宽正文', async () => {
    const { container } = await openSite([say('1', '跑这个：\n\n```bash\nmake test\n```\n')])

    expect(container.querySelector('.site-msg__body pre code')?.textContent).toBe('make test\n')
  })

  it('@人 的 token 用名册上的名字，点了把 handle 报上去', async () => {
    const { container, emitted } = await openSite([say('1', '等 <@caisongyang> 定位置')], {
      memberNames: { caisongyang: '蔡松洋' },
    })

    const chip = container.querySelector<HTMLElement>('.mention[data-handle="caisongyang"]')!
    expect(chip.textContent).toBe('@蔡松洋')

    await fireEvent.click(chip)
    expect(emitted()['mention-click']).toEqual([['caisongyang']])
  })

  it('文件 token 变 chip，点击带这条消息自己的任务 id', async () => {
    const { container, emitted } = await openSite([say('1', '见 <&frontend/src/main.ts>', 'task-9')])

    const chip = container.querySelector<HTMLElement>('.mention[data-file="frontend/src/main.ts"]')!
    expect(chip.textContent).toContain('main.ts')

    await fireEvent.click(chip)
    expect(emitted()['open-file']).toEqual([['frontend/src/main.ts', 'task-9']])
  })

  it('话题 token 点了开那个话题', async () => {
    const { container, emitted } = await openSite([say('1', '接着 <#abc12345> 往下做')])

    await fireEvent.click(container.querySelector<HTMLElement>('.topic-ref[data-topic="abc12345"]')!)
    expect(emitted()['open-topic']).toEqual([['abc12345']])
  })

  it('没点 chip 的点击什么都不发', async () => {
    const { container, emitted } = await openSite([say('1', '就是一句话')])

    await fireEvent.click(container.querySelector('.site-msg__body')!)
    expect(emitted()['mention-click']).toBeUndefined()
    expect(emitted()['open-file']).toBeUndefined()
    expect(emitted()['open-topic']).toBeUndefined()
  })
})
