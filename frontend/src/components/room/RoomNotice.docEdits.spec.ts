// The line an AI teammate's document edit leaves in the conversation. It is a
// record of the teammate's own tool call: inside its turn it does not name the
// teammate again, outside a turn it names who did it — and in neither does a
// name read as an @ mention, because the line notifies nobody. The line opens
// the document; 「查看改动」 opens it with the changes marked.
import type { Component } from 'vue'
import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { setLocale, t } from '../../i18n'
import { collapseNotices } from '../../lib/platformNotice'

import RoomNotice from './RoomNotice.vue'

const vuetify = createVuetify({ components, directives })
beforeEach(() => setLocale('zh-CN'))

const AGENT = { name: '芝士S', handle: 'cheese-a1' }
const REFS = { mentionNames: { 'li-laoshi': '李老师', 'cheese-a1': '芝士S' }, topicTitles: {} }

const EDITS = [
  { old: '数据量到一千万行时开始评估迁移。', new: '数据量到五百万行时开始评估迁移。' },
  { old: '双写一周，对账无误后切过去。', new: '双写一周，期间以 PostgreSQL 为准，对账无误后切过去。' },
]

/** A line about the conversation's own document, as the backend writes it. */
function living(meta: Record<string, unknown>, opts: { turn?: string | null } = {}): Block {
  return {
    id: 'n1',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'cheese-a1',
    content: '<@cheese-a1> 编辑了文档',
    turn_id: opts.turn === undefined ? 'turn-1' : opts.turn,
    created_at: '2026-10-02T08:00:00Z',
    meta: { action: 'doc', doc_actors: ['cheese-a1'], detail: '- 一千万\n+ 五百万', ...meta },
  } as unknown as Block
}

/** A line about a document of the project's library. */
function library(meta: Record<string, unknown>, opts: { turn?: string | null } = {}): Block {
  return {
    id: 'n2',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'cheese-a1',
    content: '<@cheese-a1> 改了文档《频道体验改进》',
    turn_id: opts.turn === undefined ? 'turn-1' : opts.turn,
    created_at: '2026-10-02T08:00:00Z',
    meta: { action: 'doc', document: { id: 'lib-doc', title: '频道体验改进' }, doc_created: false, ...meta },
  } as unknown as Block
}

/** `agent` is what the room signs the line with; null when it signs it with nobody. */
function mount(block: Block, agent: typeof AGENT | null = AGENT) {
  const [row] = collapseNotices([block])
  return render(RoomNotice as Component, {
    props: {
      block: row.block,
      notice: row.notice!,
      run: row.run,
      agent,
      time: '16:05',
      agentName: '芝士',
      refs: REFS,
    },
    global: { plugins: [vuetify] },
  })
}

function lineText(container: Element): string {
  return (container.querySelector('[data-testid="doc-edit-line"]')?.textContent ?? '').replace(/\s+/g, ' ').trim()
}

describe('a document edit inside the teammate turn that made it', () => {
  it('reads as a step of the turn: it does not name the teammate, and no name is an @ mention', () => {
    const { container } = mount(library({ doc_edits: EDITS }))

    expect(lineText(container)).toContain('频道体验改进')
    expect(lineText(container)).not.toContain('芝士S')
    expect(container.querySelector('.mention')).toBeNull()
  })

  it('says in plain words who asked for it', () => {
    const { container } = mount(living({ doc_requested_by: 'li-laoshi', doc_edits: EDITS }))

    expect(lineText(container)).toContain('李老师')
    expect(lineText(container)).not.toContain('@')
    expect(container.querySelector('.mention')).toBeNull()
  })

  it('opens the library document itself, and 「查看改动」 opens it with the changes marked', async () => {
    const view = mount(library({ doc_edits: EDITS }))

    await fireEvent.click(screen.getByRole('button', { name: /频道体验改进/ }))
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.notice.viewChanges') }))

    expect(view.emitted('open-resource')).toEqual([
      ['doc', 'turn-1', undefined, { id: 'lib-doc', title: '频道体验改进' }],
      ['doc', 'turn-1', { requester: '', edits: EDITS }, { id: 'lib-doc', title: '频道体验改进' }],
    ])
  })

  it('opens the changes someone asked for, named the way the room calls them', async () => {
    const view = mount(living({ doc_requested_by: 'li-laoshi', doc_edits: EDITS }))

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.notice.viewChanges') }))

    expect(view.emitted('open-resource')).toEqual([['doc', 'turn-1', { requester: '李老师', edits: EDITS }]])
  })

  it('opens the document for the suggestions it left', async () => {
    const view = mount(living({ doc_suggested: true, doc_suggestions: ['cheese:a', 'cheese:b'] }))

    expect(screen.queryByRole('button', { name: t('work.room.notice.viewChanges') })).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: /2/ }))

    expect(view.emitted('open-resource')).toEqual([['doc', 'turn-1']])
  })
})

describe('a document edit that belongs to no turn', () => {
  it('names who did it and who asked, in plain words and not as @ mentions', () => {
    const { container } = mount(living({ doc_requested_by: 'li-laoshi', doc_edits: EDITS }, { turn: null }), null)

    const text = lineText(container)
    expect(text).toContain('李老师')
    expect(text).toContain('芝士S')
    expect(text).not.toContain('@')
    expect(container.querySelector('.mention')).toBeNull()
  })

  it('still opens the document and its changes', async () => {
    const view = mount(library({ doc_edits: EDITS }, { turn: null }), null)

    await fireEvent.click(screen.getByRole('button', { name: /频道体验改进/ }))
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.notice.viewChanges') }))

    expect(view.emitted('open-resource')).toEqual([
      ['doc', undefined, undefined, { id: 'lib-doc', title: '频道体验改进' }],
      ['doc', undefined, { requester: '', edits: EDITS }, { id: 'lib-doc', title: '频道体验改进' }],
    ])
  })

  it('shows the change of an edit that only carries a diff where it stands', async () => {
    const { container } = mount(living({}, { turn: null }), null)

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.notice.viewChanges') }))

    expect(container.textContent).toContain('五百万')
  })
})
