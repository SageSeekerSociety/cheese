import type { AskGroupData, AskGroupSubmission } from '../lib/askGroup'

import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'
import { groupKey, makeGroupSubmission } from '../lib/askGroup'
import { groupAcknowledged, groupPendingKey, loadGroupPending } from '../lib/askGroupState'
import { emptyAskDraft, loadAskDraft, saveAskDraft } from '../lib/askState'
import { renderNoticeMessage } from '../lib/noticeText'

import { useAskGroups } from './useAskGroups'

const mocks = vi.hoisted(() => ({ read: vi.fn(), settle: vi.fn(), account: 'alice-id' }))
vi.mock('../services/askGroups', () => ({ readAskGroup: mocks.read, settleAskGroup: mocks.settle }))
vi.mock('../me', () => ({ myId: () => mocks.account }))
vi.mock('../api', () => ({
  ApiError: class extends Error {
    constructor(
      readonly status: number,
      message: string
    ) {
      super(message)
    }
  },
}))

/** The sentence a stale-version refusal arrives as: `api.ts` renders the
 *  catalog key into the reader's language before it throws. */
function staleGroupVersion(): string {
  const sentence = renderNoticeMessage({ key: 'askGroupVersionStale' }, '')
  expect(sentence).toBeTruthy()
  return sentence
}

function fixture(): AskGroupData {
  const group = { topic_id: 'room', asked_by: 'agent', id: 'group', members: ['q1', 'q2'], total: 2 }
  return {
    group,
    settlement: null,
    receipt: null,
    blocks: group.members.map((id, index) => ({
      id,
      topic_id: 'room',
      kind: 'message',
      author_type: 'participant',
      author: 'agent',
      content: `Question ${index + 1}`,
      created_at: '2026-10-01T00:00:00Z',
      meta: { options: [{ text: 'A' }, { text: 'B' }], asked: 'alice', answer_log: [], ask_group: { ...group, index } },
    })),
  }
}
function result(data: AskGroupData, payload: AskGroupSubmission): AskGroupData {
  const updated = JSON.parse(JSON.stringify(data)) as AskGroupData
  for (const item of payload.answered) {
    updated.blocks
      .find((b) => b.id === item.block_id)!
      .meta!.answer_log!.push({
        v: item.expect_version + 1,
        kind: item.kind,
        option: item.option ?? null,
        note: item.note ?? null,
        by: 'alice',
        at: null,
        client_op_id: item.client_op_id,
      })
  }
  updated.settlement = {
    v: payload.expect_version + 1,
    by: 'alice',
    at: null,
    payload_hash: 'server-hash',
    operation: {
      ...JSON.parse(JSON.stringify(payload)),
      group_id: data.group.id,
      answered: payload.answered.map((i) => ({ ...i, option: (i.option ?? '').trim(), note: (i.note ?? '').trim() })),
    },
    answered: updated.blocks.filter((b) => b.meta?.answer_log?.length).map((b) => b.id),
    later: payload.later
      .filter((i) => !updated.blocks.find((b) => b.id === i.block_id)?.meta?.answer_log?.length)
      .map((i) => i.block_id),
    unanswered: payload.unanswered
      .filter((i) => !updated.blocks.find((b) => b.id === i.block_id)?.meta?.answer_log?.length)
      .map((i) => i.block_id),
    client_op_id: payload.client_op_id,
    delivery_event_id: 'event',
  }
  return updated
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
}
function setup(data = fixture()) {
  const owner = ref('alice-id')
  const blocks = ref(data.blocks.slice(1)) // Loaded history has only one member.
  const replace = vi.fn()
  mocks.read.mockResolvedValue(data)
  const scope = effectScope()
  scopes.push(scope)
  const controller = scope.run(() =>
    useAskGroups({
      blocks: () => blocks.value,
      account: () => owner.value,
      viewer: () => (owner.value === 'alice-id' ? 'alice' : 'bob'),
      replace,
    })
  )!
  const state = () => controller.askGroups[groupKey(data.group)]!
  const choose = (id = 'q1') =>
    controller.askGroupAction(data.group, {
      type: 'question',
      blockId: id,
      action: { type: 'draft', draft: { ...emptyAskDraft(), kind: 'option', option: 'A', note: 'why' } },
    })
  return { ...controller, data, owner, blocks, replace, state, choose }
}
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  mocks.account = 'alice-id'
})
afterEach(() => {
  scopes.splice(0).forEach((s) => s.stop())
  vi.restoreAllMocks()
})

describe('atomic Ask group controller', () => {
  it('adopts current settlement from replay blocks without earlier websocket or GET', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    mocks.settle.mockRejectedValueOnce(new Error('lost response'))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    const original = JSON.parse(JSON.stringify(h.state().pending!.payload)) as AskGroupSubmission
    const v1 = result(h.data, original)
    const correction = makeGroupSubmission(v1, { q1: { ...emptyAskDraft(), kind: 'option', option: 'B' } })
    const v2 = result(v1, correction)
    v2.settlement!.delivery_event_id = 'current-event'
    for (const block of v2.blocks) block.meta!.group_settle = v2.settlement
    const oldReceipt = {
      event_id: 'event',
      state: 'received' as const,
      attempts: 1,
      last_error: null,
      sent_at: null,
      received_at: '2026-10-01T18:00:00Z',
      completed_at: null,
    }
    mocks.settle.mockResolvedValueOnce({ ...v1, blocks: v2.blocks, receipt: oldReceipt })
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    expect(h.state().pending).toBeNull()
    expect(h.state().data!.settlement!.v).toBe(2)
    expect(h.state().data!.blocks[0]!.meta!.answer_log!.at(-1)!.v).toBe(2)
    expect(h.state().data!.receipt).toBeNull()
    expect(h.state().confirmedOperation).toEqual({ settlement: v1.settlement, receipt: oldReceipt })
    expect(h.state().fresh).toBe(false)
    expect(mocks.settle.mock.calls[1]![1]).toEqual(original)
    mocks.read.mockResolvedValue(v2)
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    h.askGroupAction(h.data.group, { type: 'submit' })
    expect(mocks.settle.mock.calls[2]![1].expect_version).toBe(2)
  })

  it('confirms delayed POST v1 without rolling back websocket v2 or its new draft', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    const post = deferred<AskGroupData>()
    mocks.settle.mockReturnValue(post.promise)
    h.askGroupAction(h.data.group, { type: 'submit' })
    const payload = mocks.settle.mock.calls[0]![1] as AskGroupSubmission
    const v1 = result(h.data, payload)
    v1.receipt = {
      event_id: 'event',
      state: 'received',
      attempts: 1,
      last_error: null,
      sent_at: null,
      received_at: '2026-10-01T18:00:00Z',
      completed_at: null,
    }
    const correction = makeGroupSubmission(v1, { q1: { ...emptyAskDraft(), kind: 'option', option: 'B' } })
    const v2 = result(v1, correction)
    v2.settlement!.delivery_event_id = 'event-v2'
    for (const block of v2.blocks) block.meta!.group_settle = v2.settlement
    const draft = { ...emptyAskDraft(), kind: 'option' as const, option: 'A', note: 'new v2 draft' }
    saveAskDraft(localStorage, 'alice-id', v2.blocks[0]!, draft)
    h.blocks.value = v2.blocks
    await flush()
    h.replace.mockClear()
    post.resolve(v1)
    await flush()
    expect(h.state().pending).toBeNull()
    expect(loadGroupPending(localStorage, 'alice-id', h.data.group)).toBeNull()
    expect(h.state().error).toBeNull()
    expect(h.state().confirmedOperation).toEqual({ settlement: v1.settlement, receipt: v1.receipt })
    expect(h.state().data!.settlement!.v).toBe(2)
    expect(h.state().data!.receipt).toBeNull()
    expect(h.state().data!.blocks[0]!.meta!.answer_log!.at(-1)!.v).toBe(2)
    expect(h.replace.mock.calls.every(([block]) => block.id !== 'q1' || block.meta.answer_log.at(-1).v === 2)).toBe(
      true
    )
    expect(h.state().forms.q1!.draft).toEqual(draft)
    expect(loadAskDraft(localStorage, 'alice-id', v2.blocks[0]!)).toEqual(draft)
    expect(h.state().fresh).toBe(false)
  })

  it('confirms incremental answers using operation snapshot, not effective result lists', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.askGroupAction(h.data.group, { type: 'later', blockId: 'q2' })
    mocks.settle.mockImplementation((_scope, payload) => Promise.resolve(result(h.data, payload)))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    const first = JSON.parse(JSON.stringify(h.state().data)) as AskGroupData
    h.choose('q2')
    mocks.settle.mockImplementation((_scope, payload) => Promise.resolve(result(first, payload)))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    const payload = mocks.settle.mock.calls[1]![1] as AskGroupSubmission
    expect(payload.answered.map((i) => i.block_id)).toEqual(['q2'])
    expect(payload.unanswered.map((i) => i.block_id)).toEqual(['q1'])
    expect(h.state().data!.settlement!.answered).toEqual(['q1', 'q2'])
    expect(h.state().pending).toBeNull()
    expect(h.state().error).toBeNull()
  })

  it('releases only an explicit version rejection after refresh and user action', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    mocks.settle.mockRejectedValue(new Error('lost response'))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    const original = JSON.parse(JSON.stringify(h.state().pending!.payload))
    const other = result(
      h.data,
      makeGroupSubmission(h.data, { q1: { ...emptyAskDraft(), kind: 'option', option: 'B' } })
    )
    mocks.read.mockResolvedValue(other)
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    h.askGroupAction(h.data.group, { type: 'resolve-conflict' })
    expect(h.state().pending).not.toBeNull()
    // Another 409 of the same status, said with another sentence: the operation
    // is still not proved absent, so it stays pending.
    mocks.settle.mockRejectedValue(new ApiError(409, '同一个组 client_op_id 换了内容'))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    expect(h.state().rejectedOperation).toBeUndefined()
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    mocks.settle.mockRejectedValue(new ApiError(409, staleGroupVersion()))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    expect(mocks.settle.mock.calls.at(-1)![1]).toEqual(original)
    expect(h.state().rejectedOperation).toBe(original.client_op_id)
    h.askGroupAction(h.data.group, { type: 'resolve-conflict' })
    expect(h.state().pending).not.toBeNull()
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    h.askGroupAction(h.data.group, { type: 'resolve-conflict' })
    expect(h.state().pending).toBeNull()
    expect(loadGroupPending(localStorage, 'alice-id', h.data.group)).toBeNull()
  })

  it('updates same-version questions and preserves unknown operation without blind retry', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    mocks.settle.mockRejectedValue(new Error('lost response'))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    const original = JSON.parse(JSON.stringify(h.state().pending))
    const stale = deferred<AskGroupData>()
    mocks.read.mockReturnValueOnce(stale.promise)
    h.askGroupAction(h.data.group, { type: 'refresh' })
    const edited = fixture()
    edited.blocks[1]!.content = 'Updated question, same answer version'
    h.blocks.value = [edited.blocks[1]!]
    await flush()
    expect(h.state().data!.blocks[1]!.content).toBe(edited.blocks[1]!.content)
    stale.resolve(fixture())
    await flush()
    expect(h.state().data!.blocks[1]!.content).toBe(edited.blocks[1]!.content)
    mocks.read.mockResolvedValue(edited)
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    expect(h.state().questionChanged).toBe(true)
    h.askGroupAction(h.data.group, { type: 'submit' })
    h.askGroupAction(h.data.group, { type: 'resolve-conflict' })
    expect(mocks.settle).toHaveBeenCalledTimes(1)
    expect(h.state().pending).toEqual(original)
    expect(loadGroupPending(localStorage, 'alice-id', h.data.group)).toEqual(original)
  })

  it('loads fixed membership, selection sends nothing, back sends nothing, confirm sends one batch', async () => {
    const h = setup()
    await flush()
    expect(h.state().data!.blocks).toHaveLength(2)
    h.choose()
    expect(mocks.settle).not.toHaveBeenCalled()
    h.askGroupAction(h.data.group, { type: 'submit' })
    expect(h.state().confirm).toBe(true)
    h.askGroupAction(h.data.group, { type: 'back' })
    expect(mocks.settle).not.toHaveBeenCalled()
    mocks.settle.mockImplementation((_scope, payload) => Promise.resolve(result(h.data, payload)))
    h.askGroupAction(h.data.group, { type: 'confirm' })
    await flush()
    expect(mocks.settle).toHaveBeenCalledTimes(1)
    const payload = mocks.settle.mock.calls[0]![1] as AskGroupSubmission
    expect(payload.answered[0]).toMatchObject({ block_id: 'q1', expect_version: 0, note: 'why' })
    expect(payload.unanswered.map((i) => i.block_id)).toEqual(['q2'])
    expect(h.state().pending).toBeNull()
    expect(h.state().data!.receipt).toBeNull()
  })

  it('gates synchronous duplicate clicks and replays an identical persisted payload after failure', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    const pending = deferred<AskGroupData>()
    mocks.settle.mockReturnValue(pending.promise)
    h.askGroupAction(h.data.group, { type: 'submit' })
    h.askGroupAction(h.data.group, { type: 'submit' })
    expect(mocks.settle).toHaveBeenCalledTimes(1)
    const original = JSON.parse(JSON.stringify(mocks.settle.mock.calls[0]![1])) as AskGroupSubmission
    expect(loadGroupPending(localStorage, 'alice-id', h.data.group)!.payload).toEqual(original)
    pending.reject(new Error('lost response'))
    await flush()
    h.choose()
    mocks.settle.mockResolvedValue(result(h.data, original))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    expect(mocks.settle.mock.calls[1]![1]).toEqual(original)
    expect(h.state().pending).toBeNull()
  })

  it('refresh acknowledges a lost response without another submission', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    mocks.settle.mockRejectedValue(new Error('timeout'))
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    const payload = mocks.settle.mock.calls[0]![1] as AskGroupSubmission
    mocks.read.mockResolvedValue(result(h.data, payload))
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    expect(h.state().pending).toBeNull()
    expect(mocks.settle).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(groupPendingKey('alice-id', h.data.group))).toBeNull()
  })

  it('does not restore an older stored draft over unsaved current input', async () => {
    const h = setup()
    await flush()
    h.choose()
    const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    h.askGroupAction(h.data.group, {
      type: 'question',
      blockId: 'q1',
      action: { type: 'draft', draft: { ...emptyAskDraft(), kind: 'option', option: 'B', note: 'new input' } },
    })
    save.mockRestore()
    h.askGroupAction(h.data.group, { type: 'refresh' })
    await flush()
    expect(h.state().forms.q1!.draft).toMatchObject({ option: 'B', note: 'new input' })
  })

  it('fences responses and in-memory drafts when the account changes', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    const pending = deferred<AskGroupData>()
    mocks.settle.mockReturnValue(pending.promise)
    h.askGroupAction(h.data.group, { type: 'submit' })
    const payload = mocks.settle.mock.calls[0]![1] as AskGroupSubmission
    mocks.account = 'bob-id'
    h.owner.value = 'bob-id'
    await flush()
    h.replace.mockClear()
    pending.resolve(result(h.data, payload))
    await flush()
    expect(h.replace).not.toHaveBeenCalled()
    expect(h.state().forms.q1!.draft.kind).toBeNull()
    expect(loadGroupPending(localStorage, 'alice-id', h.data.group)).not.toBeNull()
  })

  it('never sends if durable pending storage fails', async () => {
    const h = setup()
    await flush()
    h.choose()
    h.choose('q2')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    h.askGroupAction(h.data.group, { type: 'submit' })
    await flush()
    expect(mocks.settle).not.toHaveBeenCalled()
    expect(h.state().pending).toBeNull()
    expect(h.state().storageBlocked).toBe(true)
  })

  it('rejects partial notes without misreporting a storage failure', async () => {
    const h = setup()
    await flush()
    h.askGroupAction(h.data.group, {
      type: 'question',
      blockId: 'q1',
      action: { type: 'draft', draft: { ...emptyAskDraft(), note: 'not an answer yet' } },
    })
    h.askGroupAction(h.data.group, { type: 'submit' })
    expect(mocks.settle).not.toHaveBeenCalled()
    expect(h.state().storageBlocked).toBe(false)
    expect(h.state().error).toBeTruthy()
  })

  it('rejects a group GET older than an arriving websocket answer', async () => {
    const h = setup()
    await flush()
    const old = deferred<AskGroupData>()
    mocks.read.mockReturnValueOnce(old.promise)
    h.askGroupAction(h.data.group, { type: 'refresh' })
    const latest = fixture()
    latest.blocks[1]!.meta!.answer_log = [
      { v: 1, by: 'alice', at: null, kind: 'option', option: 'B', note: null, client_op_id: 'remote-op' },
    ]
    h.blocks.value = [latest.blocks[1]!]
    await flush()
    h.replace.mockClear()
    old.resolve(fixture())
    await flush()
    expect(h.replace).not.toHaveBeenCalled()
    expect(h.state().fresh).toBe(false)
  })

  it('requires exact answer content and author to acknowledge a settlement', () => {
    const data = fixture()
    const payload = makeGroupSubmission(data, {
      q1: { ...emptyAskDraft(), kind: 'option', option: 'A', note: 'why' },
      q2: { ...emptyAskDraft(), later: true },
    })
    const pending = { account: 'alice-id', scope: groupKey(data.group), payload }
    const saved = result(data, payload)
    expect(groupAcknowledged(saved, pending, 'alice')).toBe(true)
    expect(groupAcknowledged(saved, pending, 'bob')).toBe(false)
    saved.blocks[0]!.meta!.answer_log![0]!.note = 'different'
    expect(groupAcknowledged(saved, pending, 'alice')).toBe(false)
    expect(saved.blocks[1]!.meta!.answer_log).toEqual([])
  })
})
