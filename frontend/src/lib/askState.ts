import type { AskAnswerEntry, Block } from '../cx_types'

export interface AskDraft {
  kind: 'option' | 'note' | 'reject' | null
  option: string
  note: string
  later: boolean
}

export interface AskSubmission {
  kind: AskAnswerEntry['kind']
  option?: string
  note?: string
  expect_version: number
  client_op_id: string
}

export interface AskPending {
  account: string
  topic: string
  block: string
  question: string
  payload: AskSubmission
}

export const emptyAskDraft = (): AskDraft => ({ kind: null, option: '', note: '', later: false })

export function answerVersion(block: Block): number {
  return block.meta?.answer_log?.at(-1)?.v ?? 0
}

// A question edit must not reinterpret a saved choice, even if its answer version
// has not changed. Keep the full fingerprint rather than a collision-prone hash.
export function questionIdentity(block: Block): string {
  return JSON.stringify([
    block.content,
    block.meta?.options,
    block.meta?.asked,
    block.meta?.allow_other === true,
    block.meta?.reject_option === true,
    block.meta?.ask_group ?? null,
  ])
}

export function canAnswer(block: Block, handle: string): boolean {
  if (!handle) return false
  const first = block.meta?.answer_log?.[0]
  return first ? first.by === handle : block.meta?.asked === handle
}

export function draftFromAnswer(answer: AskAnswerEntry): AskDraft {
  return { kind: answer.kind, option: answer.option ?? '', note: answer.note ?? '', later: false }
}

export function validAskDraft(block: Block, draft: AskDraft): boolean {
  if (draft.note.length > 2000 || draft.later) return false
  if (draft.kind === 'option') return !!block.meta?.options?.some((o) => o.text === draft.option)
  if (draft.kind === 'note') return block.meta?.allow_other === true && !!draft.note.trim()
  return draft.kind === 'reject' && block.meta?.reject_option === true
}

export function submittedAnswer(block: Block, pending: AskPending): AskAnswerEntry | undefined {
  const p = pending.payload
  return block.meta?.answer_log?.find(
    (a) =>
      a.client_op_id === p.client_op_id &&
      a.v === p.expect_version + 1 &&
      a.kind === p.kind &&
      (a.option ?? '') === (p.option ?? '') &&
      (a.note ?? '') === (p.note ?? '')
  )
}

function scope(account: string, block: Block): string {
  if (!account) throw new Error('ask-account-required')
  return [account, block.conversation_id, block.id].map(encodeURIComponent).join(':')
}

export function askDraftKey(account: string, block: Block, version = answerVersion(block)): string {
  return `ask-draft:${scope(account, block)}:${version}`
}

export function askPendingKey(account: string, block: Block): string {
  // Pending stays discoverable when the answer version advances after a lost
  // HTTP response. Only a server answer with this operation can acknowledge it.
  return `ask-pending:${scope(account, block)}`
}

export function loadAskDraft(storage: Storage, account: string, block: Block): AskDraft | null {
  const raw = storage.getItem(askDraftKey(account, block))
  if (!raw) return null
  const saved = JSON.parse(raw)
  if (saved.question !== questionIdentity(block)) return null
  const d = saved.draft
  if (
    !d ||
    ![null, 'option', 'note', 'reject'].includes(d.kind) ||
    typeof d.option !== 'string' ||
    typeof d.note !== 'string' ||
    typeof d.later !== 'boolean'
  )
    return null
  return d as AskDraft
}

export function saveAskDraft(storage: Storage, account: string, block: Block, draft: AskDraft): void {
  storage.setItem(askDraftKey(account, block), JSON.stringify({ question: questionIdentity(block), draft }))
}

export function loadAskPending(storage: Storage, account: string, block: Block): AskPending | null {
  const raw = storage.getItem(askPendingKey(account, block))
  if (!raw) return null
  const p = JSON.parse(raw) as AskPending
  if (
    p.account !== account ||
    p.topic !== block.conversation_id ||
    p.block !== block.id ||
    typeof p.question !== 'string' ||
    !p.payload ||
    !['option', 'note', 'reject'].includes(p.payload.kind) ||
    !Number.isInteger(p.payload.expect_version) ||
    p.payload.expect_version < 0 ||
    typeof p.payload.client_op_id !== 'string' ||
    !p.payload.client_op_id ||
    (p.payload.option !== undefined && typeof p.payload.option !== 'string') ||
    (p.payload.note !== undefined && typeof p.payload.note !== 'string')
  ) {
    // Never silently replace an unreadable operation: it may have committed.
    throw new Error('ask-pending-unreadable')
  }
  return p
}

export function prepareAskSubmission(
  storage: Storage,
  account: string,
  block: Block,
  draft: AskDraft,
  newId: () => string = () => crypto.randomUUID()
): AskPending {
  const existing = loadAskPending(storage, account, block)
  if (existing) return existing
  if (!validAskDraft(block, draft) || !draft.kind) throw new Error('ask-invalid-draft')
  const pending: AskPending = {
    account,
    topic: block.conversation_id,
    block: block.id,
    question: questionIdentity(block),
    payload: {
      kind: draft.kind,
      ...(draft.kind === 'option' ? { option: draft.option } : {}),
      ...(draft.note ? { note: draft.note } : {}),
      expect_version: answerVersion(block),
      client_op_id: newId(),
    },
  }
  // Persist before any network request; quota/privacy failures must block send.
  storage.setItem(askPendingKey(account, block), JSON.stringify(pending))
  return pending
}

export function acknowledgeAsk(storage: Storage, account: string, block: Block, handle: string): boolean {
  const pending = loadAskPending(storage, account, block)
  if (!pending || submittedAnswer(block, pending)?.by !== handle) return false
  storage.removeItem(askPendingKey(account, block))
  storage.removeItem(askDraftKey(account, block, pending.payload.expect_version))
  return true
}

export function canRevisePending(block: Block, pending: AskPending | null, fresh: boolean): boolean {
  return fresh && !!pending && answerVersion(block) > pending.payload.expect_version && !submittedAnswer(block, pending)
}
