import type { AskDraft, AskPending } from './askState'

export interface AskFormState {
  draft: AskDraft
  pending: AskPending | null
  editing: boolean
  busy: boolean
  fresh: boolean
  saved: boolean
  error: string | null
  conflict: boolean
  storageBlocked: boolean
}

export type AskAction =
  | { type: 'draft'; draft: AskDraft }
  | { type: 'submit' | 'refresh' | 'correct' | 'cancel' | 'resolve-conflict' }
