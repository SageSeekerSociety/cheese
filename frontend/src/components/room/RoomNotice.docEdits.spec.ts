// The room's line for a document change someone asked the AI teammate for
// leads to those changes in the document; the line for its suggestions leads to
// the document, where the suggestions wait.
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

const EDITS = [
  { old: '数据量到一千万行时开始评估迁移。', new: '数据量到五百万行时开始评估迁移。' },
  { old: '双写一周，对账无误后切过去。', new: '双写一周，期间以 PostgreSQL 为准，对账无误后切过去。' },
]

function notice(meta: Record<string, unknown>, content: string): Block {
  return {
    id: 'n1',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content,
    turn_id: 'turn-1',
    created_at: '2026-10-02T08:00:00Z',
    meta: { action: 'doc', detail: '- 一千万\n+ 五百万', ...meta },
  } as unknown as Block
}

function mount(block: Block) {
  const [row] = collapseNotices([block])
  return render(RoomNotice as Component, {
    props: {
      block: row.block,
      notice: row.notice!,
      run: row.run,
      agent: { name: '芝士', handle: 'cheese-a1' },
      time: '16:05',
      agentName: '芝士',
      refs: { mentionNames: { 'li-laoshi': '李老师' }, topicTitles: {} },
    },
    global: { plugins: [vuetify] },
  })
}

describe('a document change in the room', () => {
  it('opens the changes someone asked for, named the way the room calls them', async () => {
    const view = mount(notice({ doc_requested_by: 'li-laoshi', doc_edits: EDITS }, '李老师 让芝士改了文档'))

    expect(screen.getByText(t('work.room.notice.docEditCount', { n: 2 }))).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.notice.viewChanges') }))

    expect(view.emitted('open-resource')).toEqual([['doc', 'turn-1', { requester: '李老师', edits: EDITS }]])
  })

  it('opens the document for the suggestions it left', async () => {
    const view = mount(
      notice({ doc_suggested: true, doc_suggestions: ['cheese:a', 'cheese:b'] }, '芝士 提了 2 处修改建议')
    )

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.notice.viewSuggestions') }))

    expect(view.emitted('open-resource')).toEqual([['doc', 'turn-1']])
  })
})
