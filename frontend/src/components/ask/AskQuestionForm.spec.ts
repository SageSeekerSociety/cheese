import type { Block } from '../../cx_types'
import type { AskFormState } from '../../lib/askPresentation'

import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import { setLocale } from '../../i18n'
import { emptyAskDraft } from '../../lib/askState'

import AskQuestionForm from './AskQuestionForm.vue'

setLocale('zh-CN')
afterEach(cleanup)
const block = (): Block => ({
  id: 'q',
  topic_id: 't',
  kind: 'message',
  content: 'Pick',
  author_type: 'participant',
  author: 'agent',
  created_at: '',
  meta: { asked: 'alice', options: [{ text: 'A', explain: 'Explanation' }, { text: 'B' }], answer_log: [] },
})
const state = (): AskFormState => ({
  draft: emptyAskDraft(),
  pending: null,
  editing: false,
  busy: false,
  fresh: true,
  saved: false,
  error: null,
  conflict: false,
  storageBlocked: false,
})

describe('real question form', () => {
  it('keeps grouped free text behind the "write your own" option until it is chosen', async () => {
    const b = block()
    b.meta!.allow_other = true
    const s = state()
    const view = render(AskQuestionForm, {
      props: { block: b, viewer: 'alice', names: {}, state: s, grouped: true },
    })
    // 还没选「自己填写」之前，输入框不该出现：它属于那个选项，不先占着位置。
    expect(screen.queryByRole('textbox')).toBeNull()
    await fireEvent.change(screen.getByRole('radio', { name: /自己填写/ }))
    expect(view.emitted().action).toEqual([
      [{ type: 'draft', draft: { ...emptyAskDraft(), kind: 'note', option: '' } }],
    ])
    await view.rerender({ state: { ...s, draft: { ...emptyAskDraft(), kind: 'note' } } })
    await fireEvent.update(screen.getByRole('textbox', { name: '你的回答' }), '我的方案')
    expect(view.emitted().action?.at(-1)).toEqual([
      { type: 'draft', draft: { ...emptyAskDraft(), kind: 'note', option: '', note: '我的方案' } },
    ])
  })

  it('allows numbered selection outside text input, while typing digits keeps the response intact', async () => {
    const b = block()
    b.meta!.allow_other = true
    const s = state()
    const view = render(AskQuestionForm, {
      props: { block: b, viewer: 'alice', names: {}, state: s, grouped: true },
    })
    await fireEvent.keyDown(screen.getByRole('group'), { key: '2' })
    expect(view.emitted().action?.at(-1)).toEqual([
      { type: 'draft', draft: { ...emptyAskDraft(), kind: 'option', option: 'B' } },
    ])
    await view.rerender({ state: { ...s, draft: { ...emptyAskDraft(), kind: 'note' } } })
    const count = view.emitted().action!.length
    await fireEvent.keyDown(screen.getByRole('textbox'), { key: '1' })
    await fireEvent.keyDown(screen.getByRole('group'), { key: '1', isComposing: true })
    expect(view.emitted().action).toHaveLength(count)
  })

  it('selection only emits a draft; explicit submit sends the action', async () => {
    const s = state()
    const view = render(AskQuestionForm, { props: { block: block(), viewer: 'alice', names: {}, state: s } })
    expect(screen.getByText('Explanation')).toBeTruthy()
    await fireEvent.change(screen.getByRole('radio', { name: /A/ }))
    expect(view.emitted().action).toEqual([
      [{ type: 'draft', draft: { ...emptyAskDraft(), kind: 'option', option: 'A' } }],
    ])
    await view.rerender({ state: { ...s, draft: { ...emptyAskDraft(), kind: 'option', option: 'A' } } })
    const submit = screen.getByRole('button', { name: '提交答案' })
    expect(submit.hasAttribute('disabled')).toBe(false)
    // happy-dom does not synthesize the native form submission from a click.
    await fireEvent.submit(submit.closest('form')!)
    expect(view.emitted().action?.at(-1)).toEqual([{ type: 'submit' }])
  })

  it('does not invent note/reject permission and blocks other respondents', () => {
    render(AskQuestionForm, { props: { block: block(), viewer: 'bob', names: {}, state: state() } })
    expect(screen.queryByRole('radio')).toBeNull()
    expect(screen.queryByRole('button', { name: '提交答案' })).toBeNull()
    expect(screen.getByText('等待 alice 回答')).toBeTruthy()
  })

  it('preserves notes and history without claiming continuation', () => {
    const b = block()
    b.meta!.answer_log = [
      { v: 1, kind: 'option', option: 'A', note: 'Original detail', by: 'alice', at: null, client_op_id: 'one' },
      { v: 2, kind: 'option', option: 'B', note: 'Correction detail', by: 'alice', at: null, client_op_id: 'two' },
    ]
    render(AskQuestionForm, { props: { block: b, viewer: 'bob', names: {}, state: state() } })
    expect(screen.getByText('Original detail')).toBeTruthy()
    expect(screen.getByText('Correction detail')).toBeTruthy()
    expect(screen.getByText('执行者是否收到并继续处理，尚未确认')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '更正' })).toBeNull()
  })

  it('freezes pending payload and retries the original submission', async () => {
    const s = state()
    s.pending = {
      account: '1',
      topic: 't',
      block: 'q',
      question: 'fixed',
      payload: { kind: 'option', option: 'A', client_op_id: 'same', expect_version: 0 },
    }
    s.draft = { ...emptyAskDraft(), kind: 'option', option: 'A' }
    const view = render(AskQuestionForm, { props: { block: block(), viewer: 'alice', names: {}, state: s } })
    expect(screen.getByRole('button', { name: '重试原提交' })).toBeTruthy()
    expect(screen.getByRole('group').hasAttribute('disabled')).toBe(true)
    await fireEvent.submit(screen.getByRole('button', { name: '重试原提交' }).closest('form')!)
    expect(view.emitted().action).toEqual([[{ type: 'submit' }]])
  })
})
