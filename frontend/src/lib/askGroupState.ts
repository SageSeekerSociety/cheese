import type { AskGroupData, AskGroupScope, AskGroupSubmission } from './askGroup'
import type { AskFormState } from './askPresentation'
import type { AskAction } from './askPresentation'

import { groupKey } from './askGroup'

export interface AskGroupPending {
  account: string
  scope: string
  payload: AskGroupSubmission
}
export interface AskGroupState {
  scope: AskGroupScope
  anchor: string
  data: AskGroupData | null
  forms: Record<string, AskFormState>
  pending: AskGroupPending | null
  busy: boolean
  fresh: boolean
  error: string | null
  storageBlocked: boolean
  confirm: boolean
  conflict: boolean
  unavailable: boolean
}
export type AskGroupAction =
  | { type: 'question'; blockId: string; action: AskAction }
  | { type: 'refresh' | 'submit' | 'confirm' | 'back' }
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
  return pending
}

export function groupAcknowledged(data: AskGroupData, pending: AskGroupPending, handle: string): boolean {
  const s = data.settlement
  const p = pending.payload
  if (!s || s.client_op_id !== p.client_op_id || s.by !== handle || s.v !== p.expect_version + 1) return false
  const sameIds = (a: string[], b: Array<{ block_id: string }>) =>
    JSON.stringify([...a].sort()) === JSON.stringify(b.map((i) => i.block_id).sort())
  if (!sameIds(s.answered, p.answered) || !sameIds(s.later, p.later) || !sameIds(s.unanswered, p.unanswered))
    return false
  return p.answered.every((item) =>
    data.blocks
      .find((b) => b.id === item.block_id)
      ?.meta?.answer_log?.some(
        (a) =>
          a.client_op_id === item.client_op_id &&
          a.by === handle &&
          a.v === item.expect_version + 1 &&
          a.kind === item.kind &&
          (a.option ?? '') === (item.option ?? '') &&
          (a.note ?? '') === (item.note ?? '')
      )
  )
}
