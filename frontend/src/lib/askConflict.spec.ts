import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { canRevisePending, questionIdentity } from './askState'

describe('unknown operation after question edits', () => {
  it('offers revision only after answer-version advance, never a same-version text edit', () => {
    const block: Block = {
      id: 'q',
      conversation_id: 'room',
      kind: 'message',
      author_type: 'participant',
      author: 'agent',
      content: 'Original',
      created_at: '2026-10-01T00:00:00Z',
      meta: { options: [{ text: 'A' }, { text: 'B' }], asked: 'alice', answer_log: [] },
    }
    const pending = {
      account: 'alice-id',
      topic: 'room',
      block: 'q',
      question: questionIdentity(block),
      payload: { kind: 'option' as const, option: 'A', expect_version: 0, client_op_id: 'unknown-op' },
    }
    block.content = 'Edited question'
    expect(canRevisePending(block, pending, true)).toBe(false)
    block.meta!.answer_log = [
      { v: 1, kind: 'option', option: 'B', note: null, by: 'alice', at: null, client_op_id: 'competing-op' },
    ]
    expect(canRevisePending(block, pending, false)).toBe(false)
    expect(canRevisePending(block, pending, true)).toBe(true)
    block.meta!.answer_log[0]!.client_op_id = 'unknown-op'
    block.meta!.answer_log[0]!.option = 'A'
    expect(canRevisePending(block, pending, true)).toBe(false)
  })
})
