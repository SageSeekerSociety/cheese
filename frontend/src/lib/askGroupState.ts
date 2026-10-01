import type { AskGroupData, AskGroupScope, AskGroupSubmission } from './askGroup'
import type { AskFormState } from './askPresentation'
import type { AskAction } from './askPresentation'

import { groupKey } from './askGroup'
import { questionIdentity } from './askState'

export interface AskGroupPending {
  account: string
  scope: string
  payload: AskGroupSubmission
  questions?: Record<string, string>
}
export interface AskGroupState {
  scope: AskGroupScope
  anchor: string
  data: AskGroupData | null
  confirmedOperation?: Pick<AskGroupData, 'settlement' | 'receipt'>
  forms: Record<string, AskFormState>
  pending: AskGroupPending | null
  busy: boolean
  fresh: boolean
  error: string | null
  storageBlocked: boolean
  confirm: boolean
  conflict: boolean
  unavailable: boolean
  rejectedOperation?: string
  questionChanged?: boolean
}
export type AskGroupAction =
  | { type: 'question'; blockId: string; action: AskAction }
  | { type: 'refresh' | 'submit' | 'confirm' | 'back' | 'resolve-conflict' }
  | { type: 'later'; blockId: string }

export function groupPendingKey(account: string, scope: AskGroupScope): string {
  if (!account) throw new Error('ask-account-required')
  return `ask-group-pending:${encodeURIComponent(account)}:${encodeURIComponent(groupKey(scope))}`
}

export function loadGroupPending(storage: Storage, account: string, scope: AskGroupScope): AskGroupPending | null {
  const raw = storage.getItem(groupPendingKey(account, scope))
  if (!raw) return null
  const pending = JSON.parse(raw) as AskGroupPending
  const p = pending.payload
  if (
    pending.account !== account ||
    pending.scope !== groupKey(scope) ||
    !p ||
    p.topic_id !== scope.topic_id ||
    p.asked_by !== scope.asked_by ||
    !Number.isInteger(p.expect_version) ||
    p.expect_version < 0 ||
    typeof p.client_op_id !== 'string' ||
    !p.client_op_id ||
    !Array.isArray(p.answered) ||
    !Array.isArray(p.later) ||
    !Array.isArray(p.unanswered)
  )
    throw new Error('ask-pending-unreadable')
  const all = [...p.answered, ...p.later, ...p.unanswered]
  if (
    all.length !== scope.total ||
    new Set(all.map((i) => i.block_id)).size !== scope.total ||
    all.some((i) => !scope.members.includes(i.block_id) || typeof i.client_op_id !== 'string' || !i.client_op_id)
  )
    throw new Error('ask-pending-unreadable')
  if (
    p.answered.some(
      (i) =>
        !['option', 'note', 'reject'].includes(i.kind) ||
        !Number.isInteger(i.expect_version) ||
        i.expect_version < 0 ||
        (i.option !== undefined && typeof i.option !== 'string') ||
        (i.note !== undefined && typeof i.note !== 'string')
    )
  )
    throw new Error('ask-pending-unreadable')
  if (pending.questions && scope.members.some((id) => typeof pending.questions![id] !== 'string'))
    throw new Error('ask-pending-unreadable')
  return pending
}

export function groupAcknowledged(data: AskGroupData, pending: AskGroupPending, handle: string): boolean {
  const s = data.settlement
  const p = pending.payload
  if (!s || s.client_op_id !== p.client_op_id || s.by !== handle || s.v !== p.expect_version + 1) return false
  const operation = s.operation
  if (!operation || operation.group_id !== data.group.id || pending.scope !== groupKey(data.group)) return false
  const canonical = (value: AskGroupSubmission) =>
    JSON.stringify({
      topic_id: value.topic_id,
      asked_by: value.asked_by,
      expect_version: value.expect_version,
      client_op_id: value.client_op_id,
      answered: value.answered
        .map((i) => ({
          block_id: i.block_id,
          kind: i.kind.trim(),
          option: (i.option ?? '').trim(),
          note: (i.note ?? '').trim(),
          client_op_id: i.client_op_id.trim(),
          expect_version: i.expect_version,
        }))
        .sort((a, b) => a.block_id.localeCompare(b.block_id)),
      later: value.later
        .map((i) => ({ block_id: i.block_id, client_op_id: i.client_op_id }))
        .sort((a, b) => a.block_id.localeCompare(b.block_id)),
      unanswered: value.unanswered
        .map((i) => ({ block_id: i.block_id, client_op_id: i.client_op_id }))
        .sort((a, b) => a.block_id.localeCompare(b.block_id)),
    })
  if (canonical(operation) !== canonical(p)) return false
  return p.answered.every((item) =>
    data.blocks
      .find((b) => b.id === item.block_id)
      ?.meta?.answer_log?.some(
        (a) =>
          a.client_op_id === item.client_op_id &&
          a.by === handle &&
          a.v === item.expect_version + 1 &&
          a.kind === item.kind &&
          (a.option ?? '') === (item.option ?? '').trim() &&
          (a.note ?? '') === (item.note ?? '').trim()
      )
  )
}

export function groupQuestionChanged(data: AskGroupData, pending: AskGroupPending): boolean {
  return data.blocks.some((block) => pending.questions?.[block.id] !== questionIdentity(block))
}
