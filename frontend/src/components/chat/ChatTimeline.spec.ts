// The timeline hands what a person does on a row back to the panel, naming the
// row it happened on.
import type { Component } from 'vue'
import type { Block } from '../../cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { isAgentBlock } from '../../lib/authorship'
import { collapseNotices } from '../../lib/platformNotice'

import ChatTimeline from './ChatTimeline.vue'

import i18n, { setLocale } from '@/i18n'

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

function mount(overrides: Record<string, unknown> = {}) {
  const rows = collapseNotices(BLOCKS)
  return render(ChatTimeline as unknown as Component, {
    props: {
      topic: { id: 't', project_id: 'p' },
      rows,
      dayLabels: new Map(),
      unreadAnchorId: null,
      splitMarkers: { before: new Map(), tail: [] },
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
      avatarSrc: () => null,
      displayName: (m: Block) => m.author,
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
    await fireEvent.update(box, 'second, reworded')
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
