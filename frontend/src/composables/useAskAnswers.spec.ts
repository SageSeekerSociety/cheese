import type { Block } from '../cx_types'

import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { askPendingKey, emptyAskDraft } from '../lib/askState'

import { useAskAnswers } from './useAskAnswers'

const mocks = vi.hoisted(() => ({ read: vi.fn(), answer: vi.fn(), owner: 'alice-id' }))
vi.mock('../api', () => ({
  listBlocks: mocks.read,
  ApiError: class extends Error {
    status = 500
  },
}))
vi.mock('../api/answers', () => ({ answerOptions: mocks.answer }))
vi.mock('../me', () => ({ myId: () => mocks.owner, myHandle: () => (mocks.owner === 'alice-id' ? 'alice' : 'bob') }))
vi.mock('../services/account', async () => {
  const { ref } = await import('vue')
  return { currentUserId: ref('alice-id'), currentUserName: ref('alice') }
})
import { currentUserId, currentUserName } from '../services/account'

function fixture(): Block {
  return {
    id: 'q1',
    topic_id: 'room',
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: 'Choose',
    created_at: '2026-10-01T00:00:00Z',
    meta: { asked: 'alice', options: [{ text: 'A' }, { text: 'B' }], answer_log: [] },
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const scopes: ReturnType<typeof effectScope>[] = []
async function flush() {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}
function setup() {
  const blocks = ref([fixture()])
  mocks.read.mockResolvedValue({ data: blocks.value })
  const replace = vi.fn((block: Block) => {
    blocks.value = [block]
  })
  const scope = effectScope()
  scopes.push(scope)
  const controller = scope.run(() => useAskAnswers({ blocks: () => blocks.value, replace }))!
  const action = controller.askAction
  const choose = () =>
    action(blocks.value[0]!, { type: 'draft', draft: { ...emptyAskDraft(), kind: 'option', option: 'A', note: 'why' } })
  return { ...controller, blocks, replace, choose, action }
}
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  mocks.owner = 'alice-id'
  ;(currentUserId as unknown as { value: string }).value = 'alice-id'
  ;(currentUserName as unknown as { value: string }).value = 'alice'
})
afterEach(() => scopes.splice(0).forEach((s) => s.stop()))

describe('single Ask controller lifecycle', () => {
  it('blocks duplicate submit even when a websocket updates the answer during the request', async () => {
    const h = setup()
    await flush()
    h.choose()
    const pending = deferred<Block>()
    mocks.answer.mockReturnValue(pending.promise)
    h.action(h.blocks.value[0]!, { type: 'submit' })
    const payload = mocks.answer.mock.calls[0]![1]
    const updated = fixture()
    updated.meta!.answer_log = [
      { v: 1, by: 'alice', at: null, kind: 'option', option: 'A', note: 'why', client_op_id: payload.client_op_id },
    ]
    h.blocks.value = [updated]
    await flush()
    h.action(updated, { type: 'submit' })
    expect(mocks.answer).toHaveBeenCalledTimes(1)
    pending.resolve(updated)
    await flush()
    expect(h.askStates.q1!.pending).toBeNull()
  })

  it('persists and retries the original op after a lost response', async () => {
    const h = setup()
    await flush()
    h.choose()
    mocks.answer.mockRejectedValue(new Error('timeout'))
    h.action(h.blocks.value[0]!, { type: 'submit' })
    await flush()
    const first = mocks.answer.mock.calls[0]![1]
    h.action(h.blocks.value[0]!, { type: 'draft', draft: { ...emptyAskDraft(), kind: 'option', option: 'B' } })
    h.action(h.blocks.value[0]!, { type: 'submit' })
    await flush()
    expect(mocks.answer.mock.calls[1]![1]).toEqual(first)
    expect(localStorage.getItem(askPendingKey('alice-id', fixture()))).not.toBeNull()
  })

  it('ignores a late v0 GET after a websocket v1 and the v1 GET have arrived', async () => {
    const h = setup()
    await flush()
    const old = deferred<{ data: Block[] }>()
    mocks.read.mockReturnValueOnce(old.promise)
    h.action(h.blocks.value[0]!, { type: 'refresh' })
    const latest = fixture()
    latest.meta!.answer_log = [
      { v: 1, by: 'alice', at: null, kind: 'option', option: 'B', note: 'server', client_op_id: 'remote-op' },
    ]
    mocks.read.mockResolvedValue({ data: [latest] })
    h.blocks.value = [latest]
    await flush()
    h.action(latest, { type: 'correct' })
    h.action(latest, {
      type: 'draft',
      draft: { ...emptyAskDraft(), kind: 'option', option: 'A', note: 'new correction' },
    })
    old.resolve({ data: [fixture()] })
    await flush()
    expect(h.blocks.value[0]!.meta!.answer_log![0]!.v).toBe(1)
    expect(h.askStates.q1!.draft.note).toBe('new correction')
    expect(h.askStates.q1!.fresh).toBe(true)
  })

  it('ignores the old account response after a same-page account switch', async () => {
    const h = setup()
    await flush()
    h.choose()
    const pending = deferred<Block>()
    mocks.answer.mockReturnValue(pending.promise)
    h.action(h.blocks.value[0]!, { type: 'submit' })
    mocks.owner = 'bob-id'
    ;(currentUserId as unknown as { value: string }).value = 'bob-id'
    ;(currentUserName as unknown as { value: string }).value = 'bob'
    await flush()
    h.replace.mockClear()
    pending.resolve(fixture())
    await flush()
    expect(h.replace).not.toHaveBeenCalled()
    expect(h.askStates.q1!.draft.kind).toBeNull()
    expect(localStorage.getItem(askPendingKey('alice-id', fixture()))).not.toBeNull()
  })
})
