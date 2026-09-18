/** 房间里「平台自己说的话」长什么样。
 *
 * 产品约束只有两条，两条都是**看得见**的事，所以这里挂真实的 ChatPanel、喂真实
 * 的 block、从 DOM 上读结果 —— 不断言某个函数返回了什么字段：
 *
 *   1. 平台的一条提示，默认占不到三行；
 *   2. 收起来的东西一个字都不能丢，点开就在。
 *
 * 外加一条硬要求：库里存量的老事件（meta=null、meta.action、backend_error）渲染
 * 必须和改动前一模一样 —— 这张卡先于后端那张合，合的时候房间里还全是老数据。
 */
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
    answerOptions: vi.fn(),
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

import { setLocale } from '@/i18n'

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
    topic_id: roomId,
    kind: 'event',
    author_type: 'system',
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
  listBlocks.mockResolvedValue({ data: blocks.map((b) => ({ ...b, topic_id: id })), has_more: false })
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
})

// 默认语言是 en（happy-dom 的 navigator.language 是 en-US），而这份用例断言的是
// 中文界面的字。先把语言钉住，别让它跟着环境飘。
beforeEach(() => setLocale('zh-CN'))
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
    expect(shown).toContain('芝士处理中')
  })

  it('who 的三个码各渲染成一句人话', async () => {
    const { container } = mountRoom([
      ciFailed({ who: 'platform' }),
      event('', '⚠️ 采纳时合并冲突，芝士在解（5 个文件）', {
        event_type: 'accept_conflict',
        who: 'human',
        detail: 'backend/app/api/routes/accept.py',
      }),
    ])
    await flush()

    const rows = container.querySelectorAll('[data-testid="platform-notice"]')
    expect(visibleText(rows[0])).toContain('平台已处理')
    expect(visibleText(rows[1])).toContain('待人工处理')
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
      topic_id: id,
      kind: 'message',
      author_type: 'human',
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
    // 卡自己的标题和状态没变。
    expect(shown).toContain('运行环境存储空间不足')
    expect(shown).toContain('自动清理中 · 稍后 @芝士重试')
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

describe('向后兼容：库里存量的老事件一个都不能变样', () => {
  it('shows the document edit diff even beside another action in the same AI turn', async () => {
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

  it('backend_error 还是那条折叠行，traceback 仍旧一点就有', async () => {
    const { container } = mountRoom([
      event('', '💥 后端报错：ValueError: nope', {
        event_type: 'backend_error',
        stack: 'Traceback:\n  File "app/x.py", line 3\nValueError: nope',
        where: 'POST /api/topics/abc/chat',
        request_id: 'rid-1',
      }),
    ])
    await flush()

    const row = container.querySelector('[data-testid="backend-error-event"]')!
    expect(visibleText(row)).toContain('💥 后端报错：ValueError: nope')
    expect(visibleText(row)).not.toContain('Traceback:')

    expand(row)
    await flush()
    const shown = visibleText(row)
    expect(shown).toContain('Traceback:')
    expect(shown).toContain('POST /api/topics/abc/chat')
    expect(shown).toContain('rid-1')
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

  it('前端报错照旧不进房间（那是现场抽屉的东西）', async () => {
    const { container } = mountRoom([
      event('', '💥 前端报错', { event_type: 'frontend_error', stack: 'boom' }),
      event('', '话题已归档', null),
    ])
    await flush()

    expect(container.textContent).not.toContain('前端报错')
    expect(container.querySelector('.im-event')!.textContent).toContain('话题已归档')
  })
})
