/** 房间里「平台自己说的话」长什么样。
 *
 * 产品约束只有两条，两条都是**看得见**的事，所以这里挂真实的 ChatPanel、喂真实
 * 的 block、从 DOM 上读结果 —— 不断言某个函数返回了什么字段：
 *
 *   1. 平台的一条提示，默认占不到三行；
 *   2. 收起来的东西一个字都不能丢，点开就在。
 *
 * 老事件的正文、操作和日志仍可读；agent 的状态外观不改变这些内容。
 */
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

const listBlocks = vi.fn()
const listTopicMembers = vi.fn()

vi.mock('../../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    listTopicMembers: (...a: unknown[]) => listTopicMembers(...a),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    attachmentRawUrl: () => '',
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

let seq = 0
/** 每个用例一个新房间 id —— 时间线窗口有个模块级缓存，共用 id 会串味。 */
function freshRoom(): string {
  seq += 1
  return `notice-room-${seq}`
}

function room(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '平台提示',
    kind: 'topic',
    status: 'active',
    created_at: '2026-08-15T00:00:00Z',
  }
}

let blockSeq = 0
function event(roomId: string, content: string, meta: Record<string, unknown> | null): Block {
  blockSeq += 1
  return {
    id: `ev-${blockSeq}`,
    conversation_id: roomId,
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content,
    meta,
    created_at: `2026-08-15T10:${String(blockSeq).padStart(2, '0')}:00Z`,
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mountRoom(blocks: Block[]) {
  const id = freshRoom()
  listBlocks.mockResolvedValue({ data: blocks.map((b) => ({ ...b, conversation_id: id })), has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(ChatPanel, {
    props: { topic: room(id), topicList: [room(id)] },
    global: { plugins: [vuetify] },
  })
  return utils
}

/**
 * 屏幕上真读得到的字。
 *
 * 按 HTML 的规矩走：`<details>` 没 open 时，除了 `<summary>` 之外的内容对读者
 * 不存在；`hidden` / `aria-hidden` 的装饰件也不算。happy-dom 不排版，所以这是
 * 判断「收起来了没有」唯一可靠的办法 —— 用 textContent 会把折起来的东西也读出来。
 */
function visibleText(root: Element): string {
  const parts: string[] = []
  const walk = (node: Node): void => {
    if (node.nodeType === 3) {
      parts.push(node.textContent ?? '')
      return
    }
    if (node.nodeType !== 1) return
    const el = node as HTMLElement
    if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true') return
    const foldedAway = el.tagName === 'DETAILS' && !el.hasAttribute('open') && !(el as HTMLDetailsElement).open
    for (const child of Array.from(el.childNodes)) {
      if (foldedAway && !(child.nodeType === 1 && (child as Element).tagName === 'SUMMARY')) continue
      walk(child)
    }
  }
  walk(root)
  return parts.join('')
}

/** 读者点开那一下。happy-dom 不给 `<details>` 实现原生开合，所以直接置位。 */
function expand(el: Element): void {
  el.setAttribute('open', '')
  ;(el as HTMLDetailsElement).open = true
}

/**
 * 这段可见文本要占几行。
 *
 * happy-dom 没有排版，量不到真实高度。契约规定平台提示的一行 ≤40 字，就按 40 字
 * 折一行算 —— 这个折算只会高估（真实字号下一行放得下更多），所以「算出来 ≤3 行」
 * 是个保守结论。
 */
const CHARS_PER_LINE = 40
function renderedLines(text: string): number {
  return text
    .split('\n')
    .map((s) => s.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .reduce((n, line) => n + Math.max(1, Math.ceil([...line].length / CHARS_PER_LINE)), 0)
}

/** 一份货真价实的 CI 日志尾巴：4000 字符以上，逐行可辨认。 */
const CI_LOG = Array.from(
  { length: 100 },
  (_, i) => `#${i} FAILED tests/integration/test_accept.py::test_case_${i} — AssertionError: expected 200`
).join('\n')

function ciFailed(overrides: Record<string, unknown> = {}): Block {
  return event('', '⚠️ PR #123 的 CI 没过（Backend Test 等 2 个 job）', {
    event_type: 'ci_failed',
    severity: 'error',
    who: 'cheese',
    detail: CI_LOG,
    detail_label: 'CI 日志',
    ...overrides,
  })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  listTopicMembers.mockResolvedValue({ data: [] })
})

describe('agent status messages', () => {
  it.each([
    'turn_failed',
    'turn_timeout',
    'sandbox_rebuilt',
    'ci_failed',
    'pr_conflict',
    'card_filed',
    'accept_done',
    'deploy_failed',
  ])('shows %s with the agent identity while preserving the next responder and full detail', async (eventType) => {
    listTopicMembers.mockResolvedValue({
      data: [{ member_handle: 'agent-test', name: '测试助手', agent: true }],
    })
    const { container } = mountRoom([
      event('', '需要处理这次运行', { event_type: eventType, who: 'human', detail: '完整的处理说明' }),
    ])
    await flush()
    const frame = container.querySelector('.agent-status')!
    expect(frame.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe('测试助手')
    expect(visibleText(frame)).toContain('需要手动处理')
    expect(visibleText(frame)).not.toContain('完整的处理说明')
    expand(frame.querySelector('details')!)
    expect(visibleText(frame)).toContain('完整的处理说明')
  })

  it("opens the library document a line is about, not the room's, with the changes to mark", async () => {
    listTopicMembers.mockResolvedValue({ data: [{ member_handle: 'agent-test', name: '测试助手', agent: true }] })
    const made = {
      ...event('', '测试助手 新建了文档《竞品定价对比》', {
        action: 'doc',
        document: { id: 'lib-doc', title: '竞品定价对比' },
        doc_created: true,
        doc_edits: [],
      }),
      author: 'agent-test',
    }
    const changed = {
      ...event('', '测试助手 改了文档《竞品定价对比》', {
        action: 'doc',
        document: { id: 'lib-doc', title: '竞品定价对比' },
        doc_created: false,
        doc_edits: [{ old: '年付', new: '年付折扣' }],
      }),
      author: 'agent-test',
    }
    const { container, emitted } = mountRoom([made, changed])
    await flush()
    const cards = Array.from(container.querySelectorAll('button')).filter((b) =>
      b.textContent?.includes('竞品定价对比')
    )
    expect(cards).toHaveLength(2)
    cards[0].click()
    cards[1].click()
    expect(emitted()['open-resource']).toEqual([
      ['doc', undefined, undefined, { id: 'lib-doc', title: '竞品定价对比' }],
      [
        'doc',
        undefined,
        { requester: '', edits: [{ old: '年付', new: '年付折扣' }] },
        { id: 'lib-doc', title: '竞品定价对比' },
      ],
    ])
  })

  it('says a rewritten library document was updated, and how many changes were only suggested', async () => {
    listTopicMembers.mockResolvedValue({ data: [{ member_handle: 'agent-test', name: '测试助手', agent: true }] })
    const card = (meta: Record<string, unknown>) => ({
      ...event('', '测试助手 改了文档《方案》', {
        action: 'doc',
        document: { id: 'lib-doc', title: '方案' },
        doc_created: false,
        ...meta,
      }),
      author: 'agent-test',
    })
    const { container } = mountRoom([
      card({ doc_edits: [] }),
      { ...event('', '编辑者 说了一句', null), kind: 'message' as const, author: 'someone' },
      card({ doc_edits: [{ old: 'a', new: 'b' }], doc_suggested: true, doc_suggestions: ['s1'] }),
    ])
    await flush()
    const cards = Array.from(container.querySelectorAll('button')).filter((b) => b.textContent?.includes('方案'))
    expect(cards[0].textContent).toContain('已更新')
    expect(cards[0].textContent).not.toContain('改了')
    expect(cards[1].textContent).toContain('提了 1 处建议')
  })

  it('keeps a human document edit separate from an agent edit and preserves the document action', async () => {
    listTopicMembers.mockResolvedValue({
      data: [
        { member_handle: 'agent-test', name: '测试助手', agent: true },
        { member_handle: 'editor-test', name: '编辑者', agent: false },
      ],
    })
    const ai = { ...event('', '测试助手 编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const human = {
      ...event('', '编辑者 编辑了文档', { action: 'doc' }),
      author: 'editor-test',
    }
    const { container, emitted } = mountRoom([ai, human])
    await flush()
    expect(container.querySelectorAll('.agent-status')).toHaveLength(1)
    expect(container.querySelector('.agent-status')?.textContent).toContain('测试助手')
    const cards = container.querySelectorAll('.action-card')
    expect(cards).toHaveLength(2)
    expect(cards[1].closest('.agent-status')).toBeNull()
    ;(cards[0].querySelector('button') as HTMLButtonElement).click()
    expect(emitted()['open-resource']).toEqual([['doc', undefined, undefined, undefined]])
  })

  // 同一位队友连着的几件事和它连着说的几句话一样：头像和名字只出现一次。
  it('merges consecutive rows of the same agent into one line under one avatar and name', async () => {
    listTopicMembers.mockResolvedValue({
      data: [
        { member_handle: 'agent-test', name: '测试助手', agent: true },
        { member_handle: 'editor-test', name: '编辑者', agent: false },
      ],
    })
    const a = { ...event('', '测试助手 编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const b = { ...event('', '测试助手 又编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const human = { ...event('', '编辑者 编辑了文档', { action: 'doc' }), author: 'editor-test' }
    const c = { ...event('', '测试助手 第三次编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const { container } = mountRoom([a, b, human, c])
    await flush()
    // a 和 b 连着，合成一行；中间隔了别人，c 另起一行。
    const rows = Array.from(container.querySelectorAll('.agent-status'))
    expect(rows).toHaveLength(2)
    const avatars = rows.map((row) => row.querySelector('[role="img"]')?.getAttribute('aria-label') ?? null)
    expect(avatars).toEqual(['测试助手', '测试助手'])
    expect(rows[0].textContent).toContain('2')
  })

  // 同一位队友连着的几件事和它连着说的几句话一样：头像和名字只出现一次。
  it('merges consecutive rows of the same agent into one line under one avatar and name', async () => {
    listTopicMembers.mockResolvedValue({
      data: [
        { member_handle: 'agent-test', name: '测试助手', agent: true },
        { member_handle: 'editor-test', name: '编辑者', agent: false },
      ],
    })
    const a = { ...event('', '测试助手 编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const b = { ...event('', '测试助手 又编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const human = { ...event('', '编辑者 编辑了文档', { action: 'doc' }), author: 'editor-test' }
    const c = { ...event('', '测试助手 第三次编辑了文档', { action: 'doc' }), author: 'agent-test' }
    const { container } = mountRoom([a, b, human, c])
    await flush()
    // a 和 b 连着，合成一行；中间隔了别人，c 另起一行。
    const rows = Array.from(container.querySelectorAll('.agent-status'))
    expect(rows).toHaveLength(2)
    const avatars = rows.map((row) => row.querySelector('[role="img"]')?.getAttribute('aria-label') ?? null)
    expect(avatars).toEqual(['测试助手', '测试助手'])
    expect(rows[0].textContent).toContain('2')
  })

  it('does not give member events or backend logs an agent avatar', async () => {
    const { container } = mountRoom([
      event('', '编辑者加入了话题', null),
      event('', '后端日志', { event_type: 'backend_error', stack: 'Traceback: sample' }),
    ])
    await flush()
    expect(container.querySelector('.agent-status')).toBeNull()
  })

  it("does not label a worker's actual result as a status update", async () => {
    const { container } = mountRoom([
      { ...event('', '这是分身交回的完整结果', { event_type: 'subagent_stop' }), author_type: 'participant' },
    ])
    await flush()
    expect(container.querySelector('.agent-status')).toBeNull()
    expect(visibleText(container)).toContain('这是分身交回的完整结果')
  })

  it('keeps consecutive notices from different agents separate', async () => {
    const { container } = mountRoom([
      { ...ciFailed(), author: 'cheese-agentone', author_type: 'participant' },
      { ...ciFailed(), author: 'cheese-agenttwo', author_type: 'participant' },
    ])
    await flush()
    expect(container.querySelectorAll('.agent-status')).toHaveLength(2)
  })

  it('keeps two workers starting under the same room author separate', async () => {
    const { container } = mountRoom([
      event('', '分身开工', { event_type: 'subagent_start', agent_id: 'worker-one' }),
      event('', '分身开工', { event_type: 'subagent_start', agent_id: 'worker-two' }),
    ])
    await flush()
    expect(container.querySelectorAll('.agent-status')).toHaveLength(2)
  })
})

describe('平台提示：一行 + 可展开', () => {
  it('4000 字的 CI 日志默认占不到三行，而且一个字都不在屏幕上', async () => {
    expect(CI_LOG.length).toBeGreaterThan(4000)
    const { container } = mountRoom([ciFailed()])
    await flush()

    const row = container.querySelector('[data-testid="platform-notice"]')!
    expect(row).toBeTruthy()

    const shown = visibleText(row)
    expect(renderedLines(shown)).toBeLessThanOrEqual(3)
    expect(shown).not.toContain('test_case_42')
    expect(shown).not.toContain(CI_LOG)
  })

  it('点开之后，那 4000 字一字不差地在那儿', async () => {
    const { container } = mountRoom([ciFailed()])
    await flush()

    const row = container.querySelector('[data-testid="platform-notice"]')!
    expand(row)
    await flush()

    const shown = visibleText(row)
    expect(shown).toContain('CI 日志')
    // 收起来 ≠ 丢掉：整段原文逐字可读。
    expect(shown).toContain(CI_LOG)
  })

  it('不点开也看得出「出了什么事」和「谁在管」', async () => {
    const { container } = mountRoom([ciFailed()])
    await flush()

    const shown = visibleText(container.querySelector('[data-testid="platform-notice"]')!)
    expect(shown).toContain('CI 没过')
    expect(shown).toContain('芝士正在处理')
  })

  it('who 的三个码各渲染成一句人话', async () => {
    // 每一句各在自己的房间里：同一张卡后来的一句会让前一句的尾标过时（见下一组）。
    const said = async (block: Block) => {
      const { container } = mountRoom([block])
      await flush()
      return visibleText(container.querySelector('[data-testid="platform-notice"]')!)
    }

    expect(await said(ciFailed({ who: 'platform' }))).toContain('平台已处理')
    expect(
      await said(
        event('', '⚠️ 采纳时合并冲突，芝士在解（5 个文件）', {
          event_type: 'accept_conflict',
          who: 'human',
          detail: 'backend/app/api/routes/accept.py',
        })
      )
    ).toContain('需要手动处理')
  })
})

describe('平台提示：连着来的同类事件折成一条', () => {
  it('三条 ci_failed 折成一行，带 ×3', async () => {
    const { container } = mountRoom([
      ciFailed(),
      ciFailed({ detail: `${CI_LOG}\n#second run` }),
      ciFailed({ detail: `${CI_LOG}\n#third run` }),
    ])
    await flush()

    const rows = container.querySelectorAll('[data-testid="platform-notice"]')
    expect(rows).toHaveLength(1)
    expect(visibleText(rows[0])).toContain('×3')
  })

  it('折进去的每一次原话都还在，展开就能读到', async () => {
    const { container } = mountRoom([
      ciFailed(),
      ciFailed({ detail: `${CI_LOG}\n#second run` }),
      ciFailed({ detail: `${CI_LOG}\n#third run` }),
    ])
    await flush()

    const row = container.querySelector('[data-testid="platform-notice"]')!
    expand(row)
    await flush()

    const shown = visibleText(row)
    expect(shown).toContain('#second run')
    expect(shown).toContain('#third run')
  })

  it('类别不同的两条不折叠', async () => {
    const { container } = mountRoom([
      ciFailed(),
      event('', '⚠️ 质量检查没通过，卡没送出去', {
        event_type: 'gate_failed',
        who: 'cheese',
        detail: 'ruff: 3 errors',
      }),
    ])
    await flush()

    const rows = container.querySelectorAll('[data-testid="platform-notice"]')
    expect(rows).toHaveLength(2)
    expect(visibleText(rows[0])).toContain('CI 没过')
    expect(visibleText(rows[1])).toContain('质量检查没通过')
  })

  it('中间隔了一条人说的话，折叠就断开', async () => {
    const id = 'r'
    const said: Block = {
      id: 'msg-1',
      conversation_id: id,
      kind: 'message',
      author_type: 'participant',
      author: '张衡',
      content: '我看看',
      created_at: '2026-08-15T10:50:00Z',
    }
    const { container } = mountRoom([ciFailed(), said, ciFailed()])
    await flush()

    expect(container.querySelectorAll('[data-testid="platform-notice"]')).toHaveLength(2)
  })
})

describe('平台提示：事故卡的正文压成一行', () => {
  const INCIDENT =
    '这轮因运行环境存储空间不足而暂停，项目文件和已完成的改动都还在。平台正在自动清理构建缓存。清理完成后可以 @芝士 重试这一轮。'

  it('卡面只留第一句，剩下的收进展开区', async () => {
    const { container } = mountRoom([
      event('', INCIDENT, {
        event_type: 'platform_error',
        code: 'storage_exhausted',
        severity: 'error',
        title: '运行环境存储空间不足',
        retryable: true,
      }),
    ])
    await flush()

    const card = container.querySelector('[data-testid="platform-error-card"]')!
    const shown = visibleText(card)
    expect(shown).toContain('这轮因运行环境存储空间不足而暂停')
    expect(shown).not.toContain('平台正在自动清理构建缓存')
    // 卡自己的标题没变。
    expect(shown).toContain('运行环境存储空间不足')
  })

  it('剩下的那几句一句没少，点开就有', async () => {
    const { container } = mountRoom([
      event('', INCIDENT, {
        event_type: 'platform_error',
        code: 'storage_exhausted',
        title: '运行环境存储空间不足',
        retryable: true,
      }),
    ])
    await flush()

    const more = container.querySelector('[data-testid="platform-error-card"] details')!
    expand(more)
    await flush()

    const shown = visibleText(more)
    expect(shown).toContain('平台正在自动清理构建缓存')
    expect(shown).toContain('清理完成后可以 @芝士 重试这一轮')
  })
})

describe('平台提示：会话没起来', () => {
  const LOG = [
    'cheese-runner 0f0f ended: Claude Code exited with status 1 before it started:',
    'Traceback (most recent call last):',
    'executor_transport.PlatformHTTPError: Platform HTTP 504',
  ].join('\n')

  it('房间里只有那一句话，它启动时打印的原文展开了也不在房间里', async () => {
    const { container } = mountRoom([
      event('', 'Claude Code 启动失败：这个房间的工作电脑还在准备', {
        event_type: 'platform_error',
        code: 'session_start_work_machine_preparing',
        severity: 'error',
        title: '会话没有启动',
        retryable: true,
        failed: true,
        error: LOG,
      }),
    ])
    await flush()

    const card = container.querySelector('[data-testid="platform-error-card"]')!
    expect(visibleText(card)).toContain('Claude Code 启动失败：这个房间的工作电脑还在准备')
    for (const details of Array.from(container.querySelectorAll('details'))) expand(details)
    await flush()
    const everything = visibleText(container)
    for (const line of LOG.split('\n')) expect(everything).not.toContain(line)
  })
})

describe('向后兼容：库里存量的老事件一个都不能变样', () => {
  it('shows the document edit diff even folded with another action in the same AI turn', async () => {
    const doc = event('', '芝士 编辑了文档', {
      action: 'doc',
      detail_label: '查看本次修改',
      detail: '--- 修改前\n+++ 修改后\n-旧方案\n+实地调研方案',
    })
    const task = event('', '芝士 拆分了任务', { action: 'tasks' })
    doc.turn_id = 'same-turn'
    task.turn_id = 'same-turn'
    const { container } = mountRoom([doc, task])
    await flush()
    // 同一轮的两件事合成一行，点开以后改了什么照样看得到。
    ;(container.querySelector('[data-testid="notice-repeats"]') as HTMLElement).click()
    await flush()
    const details = container.querySelector('.action-card details')!
    expect(details.querySelector('summary')!.textContent).toBe('查看本次修改')
    expect(details.querySelector('.doc-edit-line--del')!.textContent).toContain('旧方案')
    expect(details.querySelector('.doc-edit-line--add')!.textContent).toContain('实地调研方案')
    expect(details.textContent).not.toContain('--- 修改前')
    expect(container.querySelectorAll('.action-card')).toHaveLength(2)
  })

  it('omits empty lines and their encoding from an old document change', async () => {
    const { container } = mountRoom([
      event('', '芝士 编辑了文档', {
        action: 'doc',
        detail: '--- 修改前\n+++ 修改后\n@@ -1 +1 @@\n <!-- PLAN -->\n 未修改的说明\n-&nbsp;\n+调查安排',
      }),
    ])
    await flush()
    const diff = container.querySelector('.doc-edit-diff')!
    expect(diff.textContent).not.toContain('空行')
    expect(diff.querySelectorAll('.doc-edit-line')).toHaveLength(1)
    expect(diff.textContent).toContain('调查安排')
    expect(diff.textContent).not.toContain('&nbsp;')
    expect(diff.textContent).not.toContain('PLAN')
    expect(diff.textContent).not.toContain('未修改的说明')
    expect(diff.textContent).not.toContain('@@')
  })

  it('meta=null 的老事件还是那条居中灰字', async () => {
    const { container } = mountRoom([event('', '话题已归档', null)])
    await flush()

    const line = container.querySelector('.im-event')!
    expect(line.textContent).toContain('话题已归档')
    expect(container.querySelector('[data-testid="platform-notice"]')).toBeNull()
  })

  it('meta.action=doc 还是那张动作行，按钮照旧', async () => {
    const { container } = mountRoom([event('', '芝士 更新了实况文档', { action: 'doc' })])
    await flush()

    const card = container.querySelector('.action-card')!
    expect(card.textContent).toContain('更新了实况文档')
    expect(card.querySelector('button')!.textContent).toContain('查看文档')
    expect(container.querySelector('[data-testid="platform-notice"]')).toBeNull()
  })

  it('内容一模一样的老事件连发，还是折成一条（老规则没丢）', async () => {
    const { container } = mountRoom([
      event('', '芝士 更新了实况文档', { action: 'doc' }),
      event('', '芝士 更新了实况文档', { action: 'doc' }),
      event('', '芝士 更新了实况文档', { action: 'doc' }),
    ])
    await flush()

    expect(container.querySelectorAll('.action-card')).toHaveLength(1)
  })

  it('有 event_type 但没有 detail 的事件，还是那条淡行（不硬塞进折叠框）', async () => {
    const { container } = mountRoom([
      event('', '机器「dev-box」连续失败，已暂停派活', { event_type: 'host_failure', who: 'human' }),
    ])
    await flush()

    expect(container.querySelector('.im-event')!.textContent).toContain('机器「dev-box」连续失败，已暂停派活')
    expect(container.querySelector('[data-testid="platform-notice"]')).toBeNull()
  })
})
