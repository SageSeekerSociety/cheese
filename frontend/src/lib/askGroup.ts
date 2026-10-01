import type { Block } from '../cx_types'
import type { AskDraft, AskSubmission } from './askState'

import { answerVersion, validAskDraft } from './askState'

export interface AskGroupScope {
  topic_id: string
  asked_by: string
  id: string
  members: string[]
  total: number
}
export interface AskGroupMeta {
  id: string
  asked_by: string
  members: string[]
  index: number
  total: number
}
export interface AskSettlement {
  v: number
  at: string | null
  by: string
  answered: string[]
  later: string[]
  unanswered: string[]
  payload_hash: string
  operation?: AskGroupSubmission & { group_id: string }
  client_op_id: string
  delivery_event_id: string
}
export interface AskReceipt {
  event_id: string
  state: 'pending' | 'claimed' | 'sending' | 'uncertain' | 'failed' | 'received' | 'unavailable'
  attempts: number
  last_error: string | null
  sent_at: string | null
  received_at: string | null
  completed_at: string | null
}
export interface AskGroupData {
  group: AskGroupScope
  blocks: Block[]
  settlement: AskSettlement | null
  receipt: AskReceipt | null
}
export interface AskGroupSubmission {
  topic_id: string
  asked_by: string
  answered: Array<AskSubmission & { block_id: string }>
  later: Array<{ block_id: string; client_op_id: string }>
  unanswered: Array<{ block_id: string; client_op_id: string }>
  expect_version: number
  client_op_id: string
}

export function groupOf(block: Block): AskGroupScope | null {
  const g = block.meta?.ask_group as AskGroupMeta | undefined
  if (
    !g ||
    typeof g.id !== 'string' ||
    typeof g.asked_by !== 'string' ||
    !Array.isArray(g.members) ||
    g.members.some((id) => typeof id !== 'string') ||
    g.total !== g.members.length ||
    g.total < 1 ||
    g.total > 8 ||
    new Set(g.members).size !== g.total ||
    !Number.isInteger(g.index) ||
    g.members[g.index] !== block.id
  )
    return null
  return { topic_id: block.topic_id, asked_by: g.asked_by, id: g.id, members: g.members, total: g.total }
}

export function groupKey(g: AskGroupScope): string {
  return JSON.stringify([g.topic_id, g.asked_by, g.id])
}

export function assertGroupData(data: AskGroupData, scope: AskGroupScope): void {
  if (
    groupKey(data.group) !== groupKey(scope) ||
    data.group.total !== scope.total ||
    JSON.stringify(data.group.members) !== JSON.stringify(scope.members) ||
    data.blocks.length !== scope.total
  ) {
    throw new Error('ask-group-membership-mismatch')
  }
  for (const [i, block] of data.blocks.entries()) {
    const g = groupOf(block)
    if (
      block.id !== scope.members[i] ||
      !g ||
      groupKey(g) !== groupKey(scope) ||
      JSON.stringify(g.members) !== JSON.stringify(scope.members)
    )
      throw new Error('ask-group-membership-mismatch')
  }
  if (data.receipt && (!data.settlement || data.receipt.event_id !== data.settlement.delivery_event_id)) {
    throw new Error('ask-group-receipt-mismatch')
  }
}

export function makeGroupSubmission(
  data: AskGroupData,
  drafts: Record<string, AskDraft | undefined>,
  newId: () => string = () => crypto.randomUUID()
): AskGroupSubmission {
  assertGroupData(data, data.group)
  const request: AskGroupSubmission = {
    topic_id: data.group.topic_id,
    asked_by: data.group.asked_by,
    answered: [],
    later: [],
    unanswered: [],
    expect_version: data.settlement?.v ?? 0,
    client_op_id: newId(),
  }
  for (const block of data.blocks) {
    const draft = drafts[block.id]
    const base = { block_id: block.id, client_op_id: newId() }
    if (draft?.later) request.later.push(base)
    else if (draft?.kind) {
      if (!validAskDraft(block, draft)) throw new Error('ask-invalid-draft')
      request.answered.push({
        ...base,
        kind: draft.kind,
        expect_version: answerVersion(block),
        ...(draft.kind === 'option' ? { option: draft.option } : {}),
        ...(draft.note ? { note: draft.note } : {}),
      })
    } else request.unanswered.push(base)
  }
  return request
}
