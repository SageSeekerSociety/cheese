// 现场的一行：点开看到的是参数原文（一行为了能扫而重写过、剪短过，摊开的人要
// 的正是被剪掉的那截），挂了的那一步还要一眼看得出来它挂了。
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

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

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
    author_type: 'participant',
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
})

async function openSite(blocks: Block[]) {
  getTranscript.mockResolvedValue({ data: blocks, total: blocks.length })
  const { container } = render(Site, {
    props: { topicId: topic.id, active: true },
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

describe('挂了的一步', () => {
  it('那一行标成失败，并写出它最后说的那截', async () => {
    const container = await openSite([
      event({
        tool: 'bash',
        arg: 'pandoc report.md -o report.docx',
        failed: true,
        error: 'bash: pandoc: command not found',
      }),
    ])

    expect(container.querySelector('.site-act--failed')).not.toBeNull()
    const error = container.querySelector('[data-testid="site-act-error"]')
    expect(error?.textContent).toBe('bash: pandoc: command not found')
  })

  it('一个没起来的会话：那一行是平台的一句话，点开错误行看到它启动时打印的全文', async () => {
    const log = [
      'cheese-runner 0f0f ended: Claude Code exited with status 1 before it started:',
      'Traceback (most recent call last):',
      'executor_transport.PlatformHTTPError: Platform HTTP 504',
    ].join('\n')
    const container = await openSite([
      {
        ...event({
          event_type: 'platform_error',
          code: 'session_start_work_machine_preparing',
          title: '会话没有启动',
          failed: true,
          error: log,
        }),
        author_type: 'platform',
        author: 'system',
        content: 'Claude Code 启动失败：这个房间的工作电脑还在准备',
      } as Block,
    ])

    expect(container.textContent).toContain('Claude Code 启动失败：这个房间的工作电脑还在准备')
    const error = container.querySelector<HTMLElement>('[data-testid="site-act-error"]')!
    expect(error.classList.contains('site-act__error--full')).toBe(false)

    await fireEvent.click(error)
    expect(error.classList.contains('site-act__error--full')).toBe(true)
    expect(error.textContent?.trim()).toBe(log)
  })

  it('成功的一步什么都不多说', async () => {
    const container = await openSite([event({ tool: 'bash', arg: 'make test' })])

    expect(container.querySelector('.site-act--failed')).toBeNull()
    expect(container.querySelector('[data-testid="site-act-error"]')).toBeNull()
  })
})
