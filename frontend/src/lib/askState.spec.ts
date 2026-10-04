import type { Block } from '../cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import {
  acknowledgeAsk,
  askDraftKey,
  askPendingKey,
  canAnswer,
  emptyAskDraft,
  loadAskDraft,
  loadAskPending,
  prepareAskSubmission,
  saveAskDraft,
  validAskDraft,
} from './askState'

const question = (): Block => ({
  id: 'question',
  topic_id: 'room',
  kind: 'message',
  author_type: 'participant',
  author: 'agent',
  content: 'Choose a direction',
  created_at: '2026-10-01T00:00:00Z',
  meta: { options: [{ text: 'A', explain: 'First choice' }, { text: 'B' }], asked: 'alice', answer_log: [] },
})
const draft = () => ({ ...emptyAskDraft(), kind: 'option' as const, option: 'A', note: 'Reason' })

beforeEach(() => localStorage.clear())

describe('Ask local state is not a server receipt', () => {
  it('isolates drafts by account, room, version and question content', () => {
    const block = question()
    saveAskDraft(localStorage, 'alice-id', block, draft())
    expect(loadAskDraft(localStorage, 'alice-id', block)).toEqual(draft())
    expect(loadAskDraft(localStorage, 'bob-id', block)).toBeNull()
    expect(loadAskDraft(localStorage, 'alice-id', { ...block, topic_id: 'other' })).toBeNull()
    expect(loadAskDraft(localStorage, 'alice-id', { ...block, content: 'Changed question' })).toBeNull()
    block.meta!.answer_log = [
      { v: 1, kind: 'option', option: 'B', note: null, by: 'alice', at: null, client_op_id: 'other' },
    ]
    expect(loadAskDraft(localStorage, 'alice-id', block)).toBeNull()
    expect(localStorage.getItem(askDraftKey('alice-id', block, 0))).not.toBeNull()
  })

  it('keeps exact operation and payload through failure, edits and reload', () => {
    const block = question()
    const original = prepareAskSubmission(localStorage, 'alice-id', block, draft(), () => 'operation-1')
    const retry = prepareAskSubmission(
      localStorage,
      'alice-id',
      block,
      { ...draft(), option: 'B' },
      () => 'must-not-use'
    )
    expect(retry).toEqual(original)
    expect(loadAskPending(localStorage, 'alice-id', block)).toEqual(original)
    expect(loadAskPending(localStorage, 'bob-id', block)).toBeNull()
    expect(block.meta!.answer_log).toEqual([])
    expect(acknowledgeAsk(localStorage, 'alice-id', block, 'alice')).toBe(false)
  })

  it('only clears after a matching authored server answer', () => {
    const block = question()
    saveAskDraft(localStorage, 'alice-id', block, draft())
    prepareAskSubmission(localStorage, 'alice-id', block, draft(), () => 'operation-1')
    block.meta!.answer_log = [
      { v: 1, kind: 'option', option: 'A', note: 'Reason', by: 'bob', at: null, client_op_id: 'operation-1' },
    ]
    expect(acknowledgeAsk(localStorage, 'alice-id', block, 'alice')).toBe(false)
    block.meta!.answer_log[0]!.by = 'alice'
    block.meta!.answer_log[0]!.note = 'Different payload'
    expect(acknowledgeAsk(localStorage, 'alice-id', block, 'alice')).toBe(false)
    block.meta!.answer_log[0]!.note = 'Reason'
    expect(acknowledgeAsk(localStorage, 'alice-id', block, 'alice')).toBe(true)
    expect(loadAskPending(localStorage, 'alice-id', block)).toBeNull()
    expect(localStorage.getItem(askDraftKey('alice-id', block, 0))).toBeNull()
  })

  it('retains an operation when a competing answer advanced the version', () => {
    const block = question()
    const pending = prepareAskSubmission(localStorage, 'alice-id', block, draft(), () => 'operation-1')
    block.meta!.answer_log = [
      { v: 1, kind: 'option', option: 'B', note: null, by: 'alice', at: null, client_op_id: 'operation-2' },
    ]
    expect(acknowledgeAsk(localStorage, 'alice-id', block, 'alice')).toBe(false)
    expect(loadAskPending(localStorage, 'alice-id', block)).toEqual(pending)
  })

  it('refuses corrupt pending data rather than replacing an unknown operation', () => {
    const block = question()
    localStorage.setItem(askPendingKey('alice-id', block), '{broken')
    expect(() => prepareAskSubmission(localStorage, 'alice-id', block, draft())).toThrow()
    expect(localStorage.getItem(askPendingKey('alice-id', block))).toBe('{broken')
  })

  it('blocks preparation when durable storage or account identity is unavailable', () => {
    const storage = {
      getItem: () => null,
      setItem: () => {
        throw new Error('quota')
      },
    } as unknown as Storage
    expect(() => prepareAskSubmission(storage, 'alice-id', question(), draft())).toThrow('quota')
    expect(() => prepareAskSubmission(localStorage, '', question(), draft())).toThrow('ask-account-required')
  })

  it('obeys persisted note/reject permission, preserving option notes', () => {
    const block = question()
    expect(validAskDraft(block, draft())).toBe(true)
    expect(validAskDraft(block, { ...draft(), option: 'Unknown' })).toBe(false)
    expect(validAskDraft(block, { ...draft(), kind: 'note' })).toBe(false)
    expect(validAskDraft(block, { ...draft(), kind: 'reject' })).toBe(false)
    block.meta!.allow_other = true
    block.meta!.reject_option = true
    expect(validAskDraft(block, { ...draft(), kind: 'note' })).toBe(true)
    expect(validAskDraft(block, { ...draft(), kind: 'note', note: '  ' })).toBe(false)
    expect(validAskDraft(block, { ...draft(), kind: 'reject' })).toBe(true)
    expect(validAskDraft(block, { ...draft(), note: 'a'.repeat(2001) })).toBe(false)
    expect(validAskDraft(block, { ...draft(), later: true })).toBe(false)
  })

  it('allows corrections only for the original author, never unknown ownership', () => {
    const block = question()
    expect(canAnswer(block, 'alice')).toBe(true)
    expect(canAnswer(block, 'bob')).toBe(false)
    block.meta!.asked = null
    expect(canAnswer(block, 'alice')).toBe(false)
    block.meta!.answer_log = [
      { v: 1, kind: 'option', option: 'A', note: null, by: 'alice', at: null, client_op_id: 'migrated' },
    ]
    expect(canAnswer(block, 'alice')).toBe(true)
    expect(canAnswer(block, 'bob')).toBe(false)
    expect(canAnswer(block, '')).toBe(false)
  })
})
