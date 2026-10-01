import type { AskGroupState } from '../../lib/askGroupState'

import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../../i18n'
import { emptyAskDraft } from '../../lib/askState'

import AskGroupFlow from './AskGroupFlow.vue'

function fixture(): AskGroupState {
  const scope = { topic_id: 'room', asked_by: 'agent', id: 'group', members: ['q1', 'q2'], total: 2 }
  return {
    scope,
    anchor: 'q1',
    pending: null,
    busy: false,
    fresh: true,
    error: null,
    storageBlocked: false,
    confirm: false,
    conflict: false,
    unavailable: false,
    forms: Object.fromEntries(
      scope.members.map((id) => [
        id,
        {
          draft: emptyAskDraft(),
          pending: null,
          editing: false,
          busy: false,
          fresh: true,
          saved: false,
          error: null,
          conflict: false,
          storageBlocked: false,
        },
      ])
    ),
    data: {
      group: scope,
      settlement: null,
      receipt: null,
      blocks: scope.members.map((id, index) => ({
        id,
        topic_id: 'room',
        kind: 'message',
        author_type: 'participant',
        author: 'agent',
        content: `问题 ${index + 1}`,
        created_at: '2026-10-01T00:00:00Z',
        meta: {
          options: [{ text: 'A', explain: '方案解释' }, { text: 'B' }],
          asked: 'alice',
          answer_log: [],
          ask_group: { ...scope, index },
        },
      })),
    },
  }
}
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('group question presentation', () => {
  it('navigates the complete group and emits a draft without submitting', async () => {
    const state = fixture()
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    await fireEvent.click(ui.getByRole('button', { name: '第 2 题' }))
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    await fireEvent.change(ui.getByRole('radio', { name: /A/ }))
    expect(ui.emitted().action).toEqual([
      [
        expect.objectContaining({
          type: 'question',
          blockId: 'q2',
          action: expect.objectContaining({ type: 'draft' }),
        }),
      ],
    ])
    expect(ui.queryByRole('button', { name: '提交答案' })).toBeNull()
  })

  it('offers back and submit-anyway separately, preserving the actual counts', async () => {
    const state = fixture()
    state.confirm = true
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    expect(ui.getByText('待提交 0 题 · 稍后 0 题 · 未答 2 题')).toBeTruthy()
    await fireEvent.click(ui.getByRole('button', { name: '回去补' }))
    await fireEvent.click(ui.getByRole('button', { name: '照样交' }))
    expect(ui.emitted().action).toEqual([[{ type: 'back' }], [{ type: 'confirm' }]])
  })

  it('opens the exact referenced member and keeps unknown receipt distinct from saved answers', () => {
    const state = fixture()
    state.data!.settlement = {
      v: 1,
      by: 'alice',
      at: null,
      answered: [],
      later: ['q1'],
      unanswered: ['q2'],
      payload_hash: 'hash',
      client_op_id: 'op',
      delivery_event_id: 'event',
    }
    state.data!.receipt = {
      event_id: 'event',
      state: 'uncertain',
      attempts: 1,
      last_error: null,
      sent_at: null,
      received_at: null,
      completed_at: null,
    }
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {}, focusBlock: 'q2' } })
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    expect(ui.getByText('答案已保存，接续结果不确定；不会盲目重发')).toBeTruthy()
    expect(ui.queryByText('原执行者已完成本次接续')).toBeNull()
  })
})
