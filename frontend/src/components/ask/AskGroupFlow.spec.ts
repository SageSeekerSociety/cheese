import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'

import { effectScope, reactive } from 'vue'
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

function fixture(members: string[] = ['q1', 'q2']): AskGroupState {
  const scope = { topic_id: 'room', asked_by: 'agent', id: 'group', members, total: members.length }
  return reactive({
    scope,
    anchor: members[0]!,
    pending: null,
    busy: false,
    fresh: true,
    error: null,
    storageBlocked: false,
    confirm: false,
    conflict: false,
    unavailable: false,
    forms: Object.fromEntries(
      members.map((id) => [
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
      blocks: members.map((id, index) => ({
        id,
        conversation_id: 'room',
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
  }) as AskGroupState
}

// 把面板发出来的动作写回 state，和真实房间里 useAskGroups 做的一样。这样断言看到的
// 才是「点下去之后界面变成什么样」，而不是某条消息的字面形状。
function apply(state: AskGroupState, action: AskGroupAction) {
  if (action.type === 'question' && action.action.type === 'draft') {
    const form = state.forms[action.blockId]
    if (form) {
      form.draft = action.action.draft
      form.editing = true
    }
  } else if (action.type === 'later') {
    const form = state.forms[action.blockId]
    if (form) form.draft = { ...form.draft, later: !form.draft.later }
  }
}

function mount(state: AskGroupState, extra: Record<string, unknown> = {}) {
  const actions: AskGroupAction[] = []
  const ui = render(AskGroupFlow, {
    props: {
      state,
      viewer: 'alice',
      names: {},
      onAction: (action: AskGroupAction) => {
        actions.push(action)
        apply(state, action)
      },
      ...extra,
    },
  })
  return { ui, actions }
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('接管输入框的一问一答', () => {
  it('defaults a single-select question to its first option without a click', async () => {
    const state = fixture()
    const { ui } = mount(state)
    await waitFor(() => expect((ui.getByRole('radio', { name: /A/ }) as HTMLInputElement).checked).toBe(true))
    expect(state.forms.q1!.draft).toMatchObject({ kind: 'option', option: 'A' })
  })

  it('holds the picked option for a beat then advances, dropping a second pick in that window', async () => {
    const state = fixture()
    const { ui } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.kind).toBe('option'))
    await fireEvent.change(ui.getByRole('radio', { name: /B/ }))
    expect(state.forms.q1!.draft.option).toBe('B')
    // 窗口内再点第一个：高亮不动，读数也不该被改回去。
    await fireEvent.change(ui.getByRole('radio', { name: /A/ }))
    expect(state.forms.q1!.draft.option).toBe('B')
    await waitFor(() => expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy())
  })

  it('submits with Enter: next question while there is one, the whole group at the end', async () => {
    const state = fixture()
    const { ui, actions } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.kind).toBe('option'))
    const card = ui.getByRole('region', { name: '整组回答' })
    await fireEvent.keyDown(card, { key: 'Enter' })
    await waitFor(() => expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy())
    await waitFor(() => expect(state.forms.q2!.draft.kind).toBe('option'))
    await fireEvent.keyDown(card, { key: 'Enter' })
    expect(actions.at(-1)).toEqual({ type: 'submit' })
  })

  it('moves the highlight with up/down and switches question with left/right', async () => {
    const state = fixture()
    const { ui } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.option).toBe('A'))
    const card = ui.getByRole('region', { name: '整组回答' })
    await fireEvent.keyDown(card, { key: 'ArrowDown' })
    expect(state.forms.q1!.draft.option).toBe('B')
    await fireEvent.keyDown(card, { key: 'ArrowUp' })
    expect(state.forms.q1!.draft.option).toBe('A')
    await fireEvent.keyDown(card, { key: 'ArrowRight' })
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    await fireEvent.keyDown(card, { key: 'ArrowLeft' })
    expect(ui.getByRole('heading', { name: '问题 1' })).toBeTruthy()
  })

  it('skip defers a question you left untouched and moves on', async () => {
    const state = fixture()
    const { ui, actions } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.kind).toBe('option'))
    await fireEvent.click(ui.getByRole('button', { name: '跳过' }))
    expect(actions.some((action) => action.type === 'later' && action.blockId === 'q1')).toBe(true)
    await waitFor(() => expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy())
  })

  it('skip carries a written free-text answer forward instead of deferring it', async () => {
    const state = fixture()
    state.data!.blocks[0]!.meta!.allow_other = true
    const { ui, actions } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.kind).toBe('option'))
    await fireEvent.change(ui.getByRole('radio', { name: /自己填写/ }))
    await fireEvent.update(ui.getByRole('textbox', { name: '你的回答' }), '我自己写的')
    await fireEvent.click(ui.getByRole('button', { name: '跳过' }))
    expect(actions.some((action) => action.type === 'later')).toBe(false)
    await waitFor(() => expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy())
  })

  it('Escape dismisses the panel without submitting or clearing the draft', async () => {
    const state = fixture()
    const { ui, actions } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.kind).toBe('option'))
    await fireEvent.keyDown(ui.getByRole('region', { name: '整组回答' }), { key: 'Escape' })
    expect(ui.emitted().dismiss).toHaveLength(1)
    expect(actions.some((action) => action.type === 'submit')).toBe(false)
    expect(state.forms.q1!.draft).toMatchObject({ kind: 'option', option: 'A' })
  })

  it('keeps a failed submission filled in and offers the original retry', async () => {
    const state = fixture(['q1'])
    state.forms.q1!.draft = { ...emptyAskDraft(), kind: 'option', option: 'B' }
    state.forms.q1!.editing = true
    state.pending = {
      account: 'alice-id',
      scope: groupKey(state.scope),
      payload: {
        topic_id: 'room',
        asked_by: 'agent',
        answered: [],
        later: [],
        unanswered: [{ block_id: 'q1', client_op_id: 'op-q1' }],
        expect_version: 0,
        client_op_id: 'op',
      },
    }
    state.error = '提交结果未确认'
    const { ui } = mount(state)
    expect(ui.getByRole('alert').textContent).toContain('提交结果未确认')
    expect((ui.getByRole('radio', { name: /B/ }) as HTMLInputElement).checked).toBe(true)
    expect(state.forms.q1!.draft.option).toBe('B')
    expect(ui.getByRole('button', { name: '重试原提交' })).toBeTruthy()
  })

  it('ignores keyboard shortcuts on a question that is no longer fresh', async () => {
    const state = fixture()
    const { ui, actions } = mount(state)
    await waitFor(() => expect(state.forms.q1!.draft.kind).toBe('option'))
    const before = actions.length
    state.forms.q1!.fresh = false
    await fireEvent.keyDown(ui.getByRole('region', { name: '整组回答' }), { key: '2' })
    expect(actions).toHaveLength(before)
  })

  it('offers a retry after the initial question group could not be loaded', async () => {
    const state = fixture()
    state.data = null
    state.error = '无法读取'
    const { ui } = mount(state)
    await fireEvent.click(ui.getByRole('button', { name: '刷新状态' }))
    expect(ui.emitted().action).toEqual([[{ type: 'refresh' }]])
  })

  it('navigates the whole group without submitting anything by itself', async () => {
    const state = fixture()
    const { ui } = mount(state)
    await fireEvent.click(ui.getByRole('button', { name: '切换问题' }))
    await fireEvent.click(ui.getByRole('button', { name: '第 2 题' }))
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    expect(ui.emitted().action).not.toContainEqual([{ type: 'submit' }])
  })

  it('offers back and submit-anyway separately, preserving the actual counts', async () => {
    const state = fixture()
    state.confirm = true
    const { ui } = mount(state)
    expect(ui.getByText('待提交 0 题 · 稍后 0 题 · 未答 2 题')).toBeTruthy()
    await fireEvent.click(ui.getByRole('button', { name: '回去补' }))
    await fireEvent.click(ui.getByRole('button', { name: '照样交' }))
    expect(ui.emitted().action).toEqual([[{ type: 'back' }], [{ type: 'confirm' }]])
  })

  it('keeps unknown receipt distinct from saved answers', () => {
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
    const { ui } = mount(state, { focusBlock: 'q2' })
    expect(ui.getByRole('heading', { name: '问题 2' })).toBeTruthy()
    expect(ui.getByText('答案已保存，接续结果不确定；不会盲目重发')).toBeTruthy()
    expect(ui.queryByText('原执行者已完成本次接续')).toBeNull()
  })

  it('keeps the selected question through a refresh driven by the real group store', async () => {
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
      const previous = state.data
      mocks.read.mockResolvedValue({ ...data, blocks: data.blocks.slice() })
      // 刷新按钮在进度那一栏里，先把那一栏展开。
      await fireEvent.click(ui.getByRole('button', { name: '切换问题' }))
      await fireEvent.click(ui.getByRole('button', { name: '刷新状态' }))
      await waitFor(() => expect(state.data).not.toBe(previous))
      expect(ui.getByRole('heading').textContent).toBe('问题 2')
    } finally {
      scope.stop()
    }
  })
})
