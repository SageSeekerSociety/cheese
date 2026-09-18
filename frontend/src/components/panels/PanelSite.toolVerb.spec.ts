// 现场那一行写的是「干了什么」，不是「调了哪个工具」。
//
// 一次 `Bash` 调用真正在做的事常常不是「执行命令」——后端认出来之后会把更贴切的
// 那个动词记在 `meta.as_tool` 上，`meta.tool` 仍然如实留着跑的是哪个工具（现场的
// 圆点分级、以及日后回头查都要靠它）。这一份钉的是这两者同时在场时，读的人看见的
// 是哪一个，以及后端还没给出覆盖时、连结构化的 meta 都没有时，这一行分别退到哪里。
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

import { setLocale } from '@/i18n'

const Site = PanelSite as unknown as Component
const CJK = /[㐀-䶿一-鿿豈-﫿]/

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

/** 一条落库的工具事件，和后端存下来的形状一样：烤好的正文 + 结构化的 meta。 */
function toolEvent(content: string, meta?: Record<string, unknown>): Block {
  return {
    id: 'e1',
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'ai',
    author: 'cheese-t1',
    content,
    reply_to: null,
    refs: [],
    meta,
    created_at: '2026-08-01T00:10:00Z',
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // 下面断言的是中文界面。动词现在从词表取，默认语言是 en（happy-dom 的
  // navigator.language 是 en-US），不钉住的话这一份会拿到英文——那是「讲英文」
  // 那组的事。
  setLocale('zh-CN')
  getTranscript.mockReset()
  getTerminal.mockReset()
  getTerminal.mockResolvedValue({ available: false })
})

/** 开一次现场，等这条事件画出来，把那一行的动词交回去。 */
async function verbOf(block: Block): Promise<{ verb: string; arg: string; text: string }> {
  getTranscript.mockResolvedValue({ data: [block], total: 1 })
  const { container } = render(Site, {
    props: { topic, active: true },
    global: { plugins: [vuetify] },
  })
  await waitFor(() => expect(container.querySelector('.site-act__verb')).not.toBeNull())
  return {
    verb: container.querySelector('.site-act__verb')?.textContent?.trim() ?? '',
    arg: container.querySelector('[data-testid="site-act-arg"]')?.textContent?.trim() ?? '',
    text: container.textContent ?? '',
  }
}

describe('现场的动作行说的是干了什么', () => {
  it('后端认出这次 Bash 其实在读文件，现场就写「读取文件」', async () => {
    const { verb, arg, text } = await verbOf(
      toolEvent('执行命令\nsed -n 1,40p backend/app/main.py', {
        tool: 'Bash',
        as_tool: 'Read',
        arg: 'backend/app/main.py',
      })
    )

    expect(verb).toBe('读取文件')
    // 「执行命令」是它真正调的那个工具的名字，不是它干的事——不该露在这一行上。
    expect(text).not.toContain('执行命令')
    expect(arg).toBe('backend/app/main.py')
  })

  it('没有更贴切的动词时，照实说调的是哪个工具', async () => {
    const { verb, arg } = await verbOf(toolEvent('搜索内容\nTODO', { tool: 'Grep', arg: 'TODO' }))

    expect(verb).toBe('搜索内容')
    expect(arg).toBe('TODO')
  })

  it('连 meta 都没有的老记录，还是按正文里烤好的那两行显示', async () => {
    const { verb, arg } = await verbOf(toolEvent('写入文件\ndocs/design-system.md'))

    expect(verb).toBe('写入文件')
    expect(arg).toBe('docs/design-system.md')
  })
})

describe('讲英文', () => {
  it('meta 算出来的动词是英文，「执行命令」不会因为它是后端给的就漏过去', async () => {
    setLocale('en')
    // 这一段正文里烤好的中文是**入库当时**写下的，面板自己不去翻它——它只借第一行
    // 认出这是哪一步。所以这里断言的是动词那一格，不是整行。
    const { verb, arg } = await verbOf(
      toolEvent('执行命令\nsed -n 1,40p backend/app/main.py', {
        tool: 'Bash',
        as_tool: 'Read',
        arg: 'backend/app/main.py',
      })
    )

    expect(verb).toBe('Read file')
    expect(CJK.test(verb), verb).toBe(false)
    expect(arg).toBe('backend/app/main.py')
  })

  it('没有 meta 的老记录也认得出是哪一步，念的是英文', async () => {
    // 老记录只有正文第一行那几个字。它是认路的凭据（数据），不是要显示的词（文案）——
    // 所以认出来之后仍然从词表取词，英文界面上不会冒出一个中文动词。
    setLocale('en')
    const { verb } = await verbOf(toolEvent('写入文件\ndocs/design-system.md'))

    expect(verb).toBe('Write file')
    expect(CJK.test(verb), verb).toBe(false)
  })
})
