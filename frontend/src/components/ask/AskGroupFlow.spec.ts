import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'

import { effectScope } from 'vue'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAskGroups } from '../../composables/useAskGroups'
import { setLocale } from '../../i18n'
import { groupKey } from '../../lib/askGroup'
import { emptyAskDraft } from '../../lib/askState'

import AskGroupFlow from './AskGroupFlow.vue'

const mocks = vi.hoisted(() => ({ read: vi.fn(), settle: vi.fn() }))
vi.mock('../../services/askGroups', () => ({ readAskGroup: mocks.read, settleAskGroup: mocks.settle }))
vi.mock('../../me', () => ({ myId: () => 'alice-id' }))
vi.mock('../../api', () => ({
  ApiError: class extends Error {
    constructor(
      readonly status: number,
      message: string
    ) {
      super(message)
    }
  },
}))

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
  it('keeps a failed pending submission visible when collapsed and preserves its draft', async () => {
    const state = fixture()
    state.busy = true
    state.forms.q1!.draft = { ...emptyAskDraft(), kind: 'option', option: 'A', note: 'keep this detail' }
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    await fireEvent.click(ui.getByRole('button', { name: '收起提问' }))
    const failed = { ...state, busy: false, error: 'Submission result unknown; original operation retained' }
    await ui.rerender({ state: failed })
    expect(ui.getByRole('alert').textContent).toBe(failed.error)
    expect(ui.queryByRole('heading', { name: '问题 1' })).toBeNull()
    expect(ui.emitted().action).toBeUndefined()
    await fireEvent.click(ui.getByRole('button', { name: '展开提问' }))
    expect((ui.getByRole('radio', { name: /A/ }) as HTMLInputElement).checked).toBe(true)
    expect((ui.getByRole('textbox') as HTMLTextAreaElement).value).toBe('keep this detail')
    expect(ui.getByRole('alert').textContent).toBe(failed.error)
  })

  it('selects a numbered draft from the card once and leaves typing or modified keys alone', async () => {
    const state = fixture()
    state.data!.blocks[0]!.meta!.allow_other = true
    state.forms.q1!.draft.note = 'Keep this detail'
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    const card = ui.getByRole('region', { name: '整组回答' })
    await fireEvent.keyDown(card, { key: '2' })
    expect(ui.emitted().action).toEqual([
      [
        {
          type: 'question',
          blockId: 'q1',
          action: { type: 'draft', draft: { ...state.forms.q1!.draft, kind: 'option', option: 'B' } },
        },
      ],
    ])
    await fireEvent.keyDown(ui.getAllByRole('radio')[0]!, { key: '1' })
    expect(ui.emitted().action).toHaveLength(2)
    const reply = ui.getByRole('textbox', { name: '你的回答' })
    await fireEvent.keyDown(reply, { key: '2' })
    await fireEvent.keyDown(card, { key: '1', isComposing: true })
    await fireEvent.keyDown(card, { key: '1', ctrlKey: true })
    await fireEvent.keyDown(card, { key: '1', altKey: true })
    await fireEvent.keyDown(card, { key: '1', metaKey: true })
    expect(ui.emitted().action).toHaveLength(2)
    await fireEvent.keyDown(card, { key: '3' })
    expect(ui.emitted().action?.at(-1)).toEqual([
      {
        type: 'question',
        blockId: 'q1',
        action: { type: 'draft', draft: { ...state.forms.q1!.draft, kind: 'note', option: '' } },
      },
    ])
    await waitFor(() => expect(document.activeElement).toBe(reply))
    expect(ui.emitted().action).toHaveLength(3)
  })

  it('routes deliberate question navigation to the answer while refresh preserves another input focus', async () => {
    const state = fixture()
    state.data!.blocks[1]!.meta!.options = []
    state.data!.blocks[1]!.meta!.allow_other = true
    const external = document.createElement('textarea')
    document.body.append(external)
    external.focus()
    try {
      const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {}, focusBlock: 'q1' } })
      expect(document.activeElement).toBe(external)
      await fireEvent.click(ui.getByRole('button', { name: '下一题' }))
      await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('textbox', { name: '你的回答' })))
      await fireEvent.click(ui.getByRole('button', { name: '上一题' }))
      await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('region', { name: '整组回答' })))
      await ui.rerender({ autoFocus: true, focusBlock: 'q2' })
      await waitFor(() => expect(document.activeElement).toBe(ui.getByRole('textbox', { name: '你的回答' })))
      external.focus()
      await ui.rerender({ state: { ...state, data: { ...state.data!, blocks: state.data!.blocks.slice() } } })
      expect(document.activeElement).toBe(external)
    } finally {
      external.remove()
    }
  })

  it('does not offer a numbered custom shortcut beyond nine or edit a locked question', async () => {
    const state = fixture()
    state.data!.blocks[0]!.meta!.options = Array.from({ length: 9 }, (_, i) => ({ text: `Option ${i + 1}` }))
    state.data!.blocks[0]!.meta!.allow_other = true
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    const card = ui.getByRole('region', { name: '整组回答' })
    await fireEvent.keyDown(card, { key: '9' })
    expect(ui.emitted().action?.at(-1)).toEqual([
      expect.objectContaining({
        action: expect.objectContaining({ draft: expect.objectContaining({ option: 'Option 9' }) }),
      }),
    ])
    await fireEvent.keyDown(card, { key: '0' })
    await fireEvent.keyDown(card, { key: '10' })
    expect(ui.emitted().action).toHaveLength(1)
    await ui.rerender({ state: { ...state, forms: { ...state.forms, q1: { ...state.forms.q1!, fresh: false } } } })
    await fireEvent.keyDown(card, { key: '1' })
    expect(ui.emitted().action).toHaveLength(1)
  })

  it('offers a retry after the initial question group could not be loaded', async () => {
    const state = fixture()
    state.data = null
    state.error = '无法读取'
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    await fireEvent.click(ui.getByRole('button', { name: '刷新状态' }))
    expect(ui.emitted().action).toEqual([[{ type: 'refresh' }]])
  })
  it('keeps the selected question through refresh while following new references and groups', async () => {
    const data = fixture().data!
    mocks.read.mockResolvedValue(data)
    const scope = effectScope()
    try {
      const controller = scope.run(() =>
        useAskGroups({
          blocks: () => data.blocks,
          account: () => 'alice-id',
          viewer: () => 'alice',
          replace: () => {},
        })
      )!
      const state = controller.askGroups[groupKey(data.group)]!
      const ui = render(AskGroupFlow, {
        props: {
          state,
          viewer: 'alice',
          names: {},
          focusBlock: 'q2',
          onAction: (action: AskGroupAction) => controller.askGroupAction(state.scope, action),
        },
      })
      await waitFor(() => expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy())
      await fireEvent.click(ui.getByRole('button', { name: '切换问题' }))
      await fireEvent.click(ui.getByRole('button', { name: '第 1 题' }))
      expect(ui.getByRole('heading', { name: '问题 1' })).toBeTruthy()
      const previous = state.data
      mocks.read.mockResolvedValue({ ...data, blocks: data.blocks.slice() })
      await fireEvent.click(ui.getByRole('button', { name: '切换问题' }))
      await fireEvent.click(ui.getByRole('button', { name: '刷新状态' }))
      await waitFor(() => expect(state.data).not.toBe(previous))
      expect(ui.getByRole('heading').textContent).toBe('问题 1')

      await ui.rerender({ focusBlock: 'q1' })
      await ui.rerender({ focusBlock: 'q2' })
      expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()

      const next = fixture()
      next.scope = { ...next.scope, id: 'next-group', members: ['q3', 'q4'] }
      next.forms = { q3: next.forms.q1!, q4: next.forms.q2! }
      next.data = {
        ...next.data!,
        group: next.scope,
        blocks: next.data!.blocks.map((block, index) => ({
          ...block,
          id: next.scope.members[index]!,
          content: `下一组问题 ${index + 1}`,
          meta: { ...block.meta, ask_group: { ...next.scope, index } },
        })),
      }
      await ui.rerender({ state: next, focusBlock: 'q4' })
      expect(ui.getByRole('heading', { name: '下一组问题 2' })).toBeTruthy()
      await ui.rerender({ state: fixture(), focusBlock: undefined })
      expect(ui.getByRole('heading', { name: '问题 1' })).toBeTruthy()
    } finally {
      scope.stop()
    }
  })

  it('can dismiss and reopen a question without discarding drafts or sending an answer', async () => {
    const state = fixture()
    state.forms.q1!.draft = { ...emptyAskDraft(), kind: 'option', option: 'A' }
    state.forms.q1!.editing = true
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    await fireEvent.click(ui.getByRole('button', { name: '收起提问' }))
    expect(ui.queryByRole('heading', { name: '问题 1' })).toBeNull()
    await fireEvent.click(ui.getByRole('button', { name: '展开提问' }))
    expect((ui.getByRole('radio', { name: /A/ }) as HTMLInputElement).checked).toBe(true)
    await fireEvent.keyDown(ui.getByRole('region', { name: '整组回答' }), { key: 'Escape' })
    expect(ui.queryByRole('heading', { name: '问题 1' })).toBeNull()
    expect(ui.emitted().action).toBeUndefined()
  })
  it('moves between questions from the header while leaving text-entry keys alone', async () => {
    const ui = render(AskGroupFlow, { props: { state: fixture(), viewer: 'alice', names: {} } })
    await fireEvent.click(ui.getByRole('button', { name: '下一题' }))
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    await fireEvent.keyDown(ui.getByRole('textbox'), { key: 'ArrowLeft' })
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    await fireEvent.keyDown(ui.getByRole('region', { name: '整组回答' }), { key: 'ArrowLeft' })
    expect(ui.getByRole('heading', { name: '问题 1' })).toBeTruthy()
    expect(ui.emitted().action).toBeUndefined()
  })

  it('navigates the complete group and emits a draft without submitting', async () => {
    const state = fixture()
    const ui = render(AskGroupFlow, { props: { state, viewer: 'alice', names: {} } })
    await fireEvent.click(ui.getByRole('button', { name: '切换问题' }))
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
