// The timeline hands what a person does on a row back to the panel, naming the
// row it happened on.
import type { Component } from 'vue'
import type { Block, ReplyPreview } from '../../cx_types'
import type { TaskLine } from '../../lib/channelTasks'
import type { ChapterRow } from '../../types/chapters'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { isAgentBlock } from '../../lib/authorship'
import { collapseNotices } from '../../lib/platformNotice'
import { taskState } from '../../lib/taskState'

import ChatTimeline from './ChatTimeline.vue'

import i18n, { setLocale } from '@/i18n'
import { fillComposer } from '@/test/composerInput'
import { loadMarkdownReader } from '@/test/markdownReader'

function said(id: string, content: string): Block {
  return {
    id,
    conversation_id: 't',
    kind: 'message',
    author_type: 'participant',
    author: 'me',
    content,
    created_at: '2026-09-01T10:00:00Z',
  } as Block
}

const BLOCKS = [said('a', 'first'), said('b', 'second'), said('c', 'third')]
/** 一件等看的人审阅的任务，卡上那一格的状态。 */
const REVIEW = taskState(
  { id: 'task', presentation: { column: 'needs_you', phrase: 'awaiting_review' }, waiting_on: 'lin' },
  { viewer: 'lin', agentName: '芝士' }
)

function mount(overrides: Record<string, unknown> = {}) {
  const rows = collapseNotices(BLOCKS)
  return render(ChatTimeline as unknown as Component, {
    props: {
      topic: { id: 't', project_id: 'p' },
      rows,
      dayLabels: new Map(),
      unreadAnchorId: null,
      runEdges: rows.map((_, i) => (i ? 'cont' : 'start')),
      arrived: new Set(),
      delivered: new Set(),
      sentNow: new Set(),
      flashId: null,
      timeShownId: null,
      bar: { id: null, shown: false, top: 0, jump: false },
      barBlock: null,
      barEditable: false,
      reactionPickerFor: null,
      loadingHistory: false,
      hasMore: false,
      loadingOlder: false,
      retryIndex: -1,
      retryBusy: false,
      working: false,
      showStarters: false,
      starterPrompts: [],
      agentSeat: undefined,
      agentName: '芝士',
      refs: { mentionNames: {}, topicTitles: {} },
      outbox: [],
      typing: [],
      editingId: null,
      editSaving: false,
      askBusy: null,
      scrollRef: ref(null),
      contentRef: ref(null),
      isAgentBlock,
      isMine: () => true,
      isExternal: () => false,
      avatarOf: () => null,
      displayName: (m: Block | ReplyPreview) => m.author,
      noticeAgent: () => null,
      parentOf: () => undefined,
      showReplyCue: () => false,
      fmtTime: (iso: string) => iso.slice(11, 16),
      pendingBlock: () => BLOCKS[0],
      outgoingState: () => '',
      outboxEdge: () => 'start',
      myName: 'me',
      viewer: 'me',
      ...overrides,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

beforeAll(loadMarkdownReader)

describe('a row tells the panel which row it is', () => {
  it('the fade-in that ended belongs to the row it played on', async () => {
    const { container, emitted } = mount({ arrived: new Set(['b']) })
    const row = container.querySelector('[data-mid="b"]')!
    await fireEvent.animationEnd(row, { animationName: 'tl-arrive-x1' })
    const settled = emitted('settle-arrival') as [AnimationEvent, string][]
    expect(settled.map(([, id]) => id)).toEqual(['b'])
  })

  it('saving an edit saves the message being edited', async () => {
    const { emitted } = mount({ editingId: 'b', barEditable: true })
    const box = screen.getByRole('textbox')
    await fillComposer(box, 'second, reworded')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))
    const saved = emitted('save-edit') as [Block, string][]
    expect(saved.map(([block, text]) => [block.id, text])).toEqual([['b', 'second, reworded']])
  })
})

describe('screen readers hear newly arrived messages, once each', () => {
  it('announces who and the start of a message from someone else that just arrived', async () => {
    const view = mount({ isMine: () => false })
    const live = view.container.querySelector('[data-testid="chat-live"]')!
    expect(live.getAttribute('aria-live')).toBe('polite')
    expect(live.textContent).toBe('')
    const next = [...BLOCKS, { ...said('d', 'the build is green'), author: 'cheese' }]
    await view.rerender({ rows: collapseNotices(next), arrived: new Set(['d']) })
    expect(live.textContent).toContain('cheese')
    expect(live.textContent).toContain('the build is green')
  })

  it('stays quiet for history that loaded and for my own messages', async () => {
    const view = mount({ isMine: () => true })
    const live = view.container.querySelector('[data-testid="chat-live"]')!
    await view.rerender({ rows: collapseNotices([...BLOCKS, said('d', 'mine')]), arrived: new Set(['d']) })
    expect(live.textContent).toBe('')
    const other = mount({ isMine: () => false })
    const quiet = other.container.querySelector('[data-testid="chat-live"]')!
    await other.rerender({ rows: collapseNotices([...BLOCKS, said('e', 'old')]), arrived: new Set() })
    expect(quiet.textContent).toBe('')
  })
})

describe('each task made on its own in the channel keeps its own card', () => {
  // Both made before anyone named them, so the line the platform writes is word for word the same.
  function created(id: string, taskId: string, at: string): Block {
    return {
      id,
      conversation_id: 't',
      kind: 'event',
      author_type: 'platform',
      author: 'system',
      content: '<@lin> 创建了任务「新任务」，由 <@lin> 负责',
      created_at: at,
      meta: { platform: true, action: 'task_created', task_id: taskId },
    } as Block
  }
  function card(id: string, title: string): TaskLine {
    return { id, title, owner: 'lin', creator: 'lin', state: REVIEW, mine: true, accepted: null, at: '' }
  }

  it('two tasks created one after the other by the same person show two cards', () => {
    const tasks = new Map([
      ['t3', card('t3', 'Fix the login redirect')],
      ['t4', card('t4', 'Draft the release notes')],
    ])
    const blocks = [created('n3', 't3', '2026-10-09T22:27:21Z'), created('n4', 't4', '2026-10-09T23:19:45Z')]
    const rows = collapseNotices(blocks)
    mount({ rows, runEdges: rows.map(() => 'start'), taskOf: (id: string) => tasks.get(id) ?? null })
    const posts = screen.getAllByTestId('task-created-post')
    expect(posts.map((post) => post.getAttribute('data-row-id'))).toEqual(['n3', 'n4'])
    expect(screen.getByText('Draft the release notes')).toBeTruthy()
  })
})

describe('every row in the timeline has a name, on screen or not', () => {
  // A row off screen is not rendered (content-visibility) and the browser then has
  // no contents to name it from; emptying the rows stands in for that.
  function event(id: string, content: string, meta: Record<string, unknown> = {}): Block {
    return {
      id,
      conversation_id: 't',
      kind: 'event',
      author_type: 'platform',
      author: 'system',
      content,
      created_at: '2026-09-01T10:00:00Z',
      meta: { platform: true, ...meta },
    } as Block
  }

  it('platform lines and created tasks keep their name with nothing drawn', () => {
    const blocks = [
      said('a', 'first'),
      event('n1', '平台 PR #1459 已在 GitHub 上被人工合并'),
      said('b', 'second'),
      event('n2', '<@lin> 更新了文档「周报」'),
      said('c', 'third'),
      event('n3', '<@lin> 创建了任务「新任务」，由 <@lin> 负责', { action: 'task_created', task_id: 't3' }),
    ]
    const rows = collapseNotices(blocks)
    const task: TaskLine = {
      id: 't3',
      title: 'Fix the login redirect',
      owner: 'lin',
      creator: 'lin',
      state: REVIEW,
      mine: true,
      accepted: null,
      at: '',
    }
    const { container } = mount({
      rows,
      runEdges: rows.map(() => 'start'),
      refs: { mentionNames: { lin: '林' }, topicTitles: {} },
      nameOf: (handle: string) => (handle === 'lin' ? '林' : handle),
      noticeAgent: (b: Block) => (b.id === 'n2' ? { name: '芝士', handle: 'cheese' } : null),
      taskOf: (id: string) => (id === 't3' ? task : null),
    })
    const items = [...container.querySelectorAll('[data-feed-item]')]
    expect(items).toHaveLength(6)
    for (const item of items) item.replaceChildren()

    expect(screen.getByRole('article', { name: '10:00：平台 PR #1459 已在 GitHub 上被人工合并' })).toBeTruthy()
    expect(screen.getByRole('article', { name: '芝士，10:00：林 更新了文档「周报」' })).toBeTruthy()
    expect(screen.getByRole('article', { name: '林，10:00：新建了任务 Fix the login redirect' })).toBeTruthy()
    expect(screen.getByRole('article', { name: 'me，10:00：second' })).toBeTruthy()
  })
})

describe('a chapter starts where someone marked it', () => {
  function chapter(block: Block): ChapterRow {
    return { pinned_by: 'alice', pinned_at: '2026-10-21T00:00:00Z', title: null, block }
  }

  it('writes the chapter number and the message it starts, right above that message', () => {
    const { container, getAllByRole } = mount({ chapters: [chapter(BLOCKS[1])] })
    const marks = getAllByRole('heading', { level: 3 })
    expect(marks).toHaveLength(1)
    expect(marks[0]!.textContent).toContain('书签 1')
    expect(marks[0]!.textContent).toContain('second')
    expect(container.querySelector('[data-mid="b"]')!.previousElementSibling).toBe(marks[0])
  })

  it('numbers chapters in reading order, not the order they were marked', () => {
    const { getAllByRole } = mount({ chapters: [chapter(BLOCKS[2]), chapter(BLOCKS[0])] })
    const texts = getAllByRole('heading', { level: 3 }).map((mark) => mark.textContent)
    expect(texts[0]).toContain('书签 1')
    expect(texts[0]).toContain('first')
    expect(texts[1]).toContain('书签 2')
    expect(texts[1]).toContain('third')
  })

  it('prefers the bookmark’s own title when one is set', () => {
    const { getAllByRole } = mount({
      chapters: [{ ...chapter(BLOCKS[1]), title: '空白会话问候' }],
    })
    const marks = getAllByRole('heading', { level: 3 })
    expect(marks).toHaveLength(1)
    expect(marks[0]!.textContent).toContain('书签 1')
    expect(marks[0]!.textContent).toContain('空白会话问候')
  })

  it('says nothing above a message nobody marked', () => {
    const { queryAllByRole } = mount({ chapters: [] })
    expect(queryAllByRole('heading', { level: 3 })).toHaveLength(0)
  })
})

describe('a merged line someone opened stays open', () => {
  // The same person archives channels one after another: one merged line.
  function archived(id: string, room: string, minute: number): Block {
    return {
      id,
      conversation_id: 't',
      kind: 'event',
      author_type: 'platform',
      author: 'wang',
      content: `<@wang> archived 「${room}」`,
      created_at: `2026-09-01T10:${String(minute).padStart(2, '0')}:00Z`,
      meta: { platform: true, i18n: { content: { key: 'roomArchived', params: { actor: '<@wang>', room } } } },
    } as unknown as Block
  }

  it('shows the one that just joined without folding back up', async () => {
    const first = [archived('x1', 'weekly', 1), archived('x2', 'hiring', 2)]
    const view = mount({ rows: collapseNotices(first), runEdges: ['start'] })
    await fireEvent.click(screen.getByTestId('notice-repeats'))
    expect(view.container.textContent).toContain('hiring')

    const next = [...first, archived('x3', 'budget', 3)]
    await view.rerender({ rows: collapseNotices(next), runEdges: ['start'], arrived: new Set(['x3']) })

    expect(screen.getByTestId('notice-repeats').getAttribute('aria-expanded')).toBe('true')
    expect(view.container.textContent).toContain('weekly')
    expect(view.container.textContent).toContain('budget')
  })

  it('stays open when an older page brings an earlier one into it', async () => {
    const later = [archived('y2', 'hiring', 2), archived('y3', 'budget', 3)]
    const view = mount({ rows: collapseNotices(later), runEdges: ['start'] })
    await fireEvent.click(screen.getByTestId('notice-repeats'))

    const all = [archived('y1', 'weekly', 1), ...later]
    await view.rerender({ rows: collapseNotices(all), runEdges: ['start'] })

    expect(screen.getByTestId('notice-repeats').getAttribute('aria-expanded')).toBe('true')
    expect(view.container.textContent).toContain('weekly')
  })
})
