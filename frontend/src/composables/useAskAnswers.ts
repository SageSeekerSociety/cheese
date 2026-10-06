import type { Block } from '../cx_types'
import type { AskAction, AskFormState } from '../lib/askPresentation'

import { computed, reactive, watch } from 'vue'

import { ApiError, listBlocks } from '../api'
import { answerOptions } from '../api/answers'
import { t } from '../i18n'
import {
  acknowledgeAsk,
  answerVersion,
  askDraftKey,
  askPendingKey,
  canAnswer,
  canRevisePending,
  draftFromAnswer,
  emptyAskDraft,
  loadAskDraft,
  loadAskPending,
  prepareAskSubmission,
  questionIdentity,
  saveAskDraft,
  submittedAnswer,
  validAskDraft,
} from '../lib/askState'
import { askOptions } from '../lib/blockDisplay'
import { myHandle, myId } from '../me'
import { currentUserId, currentUserName } from '../services/account'

export function useAskAnswers(options: { blocks: () => Block[]; replace: (block: Block) => void }) {
  const states = reactive<Record<string, AskFormState>>({})
  const viewer = computed(() => currentUserName.value ?? myHandle())
  const account = computed(() => String(currentUserId.value ?? myId()))
  const identities = new Map<string, string>()
  const sending = new Map<string, symbol>()
  let generation = 0

  function active(owner: string, epoch: number): boolean {
    return owner === myId() && owner === account.value && epoch === generation
  }

  function hydrate(block: Block): AskFormState {
    const state: AskFormState = {
      draft: emptyAskDraft(),
      pending: null,
      editing: false,
      busy: false,
      fresh: false,
      saved: false,
      error: null,
      conflict: false,
      storageBlocked: false,
    }
    if (!account.value) return state
    try {
      state.pending = loadAskPending(localStorage, account.value, block)
      const draft = loadAskDraft(localStorage, account.value, block)
      if (state.pending) {
        const p = state.pending.payload
        state.draft = { kind: p.kind, option: p.option ?? '', note: p.note ?? '', later: false }
        state.editing = true
      } else if (draft) {
        state.draft = draft
        state.saved = true
        state.editing = true
      }
    } catch {
      state.storageBlocked = true
      state.error = t('ask.flow.storageError')
    }
    return state
  }

  watch(
    account,
    () => {
      generation++
      sending.clear()
      identities.clear()
      for (const id of Object.keys(states)) delete states[id]
    },
    { flush: 'sync' }
  )

  watch(
    () => [account.value, options.blocks()] as const,
    () => {
      for (const block of options.blocks()) {
        if (!askOptions(block) || block.meta?.ask_group) continue
        const identity = `${questionIdentity(block)}:${answerVersion(block)}`
        if (identities.get(block.id) === identity || sending.has(block.id)) continue
        identities.set(block.id, identity)
        states[block.id] = hydrate(block)
        // A cached timeline is not permission to submit an old version after reload.
        void refresh(block)
      }
    },
    { immediate: true, deep: true }
  )

  async function fetchQuestion(block: Block): Promise<Block> {
    const page = await listBlocks(block.conversation_id, { around: block.id, limit: 3 })
    const fresh = page.data.find((b) => b.id === block.id)
    if (!fresh) throw new Error(t('ask.flow.missing'))
    return fresh
  }

  async function refresh(block: Block): Promise<void> {
    const state = states[block.id]
    if (!state || state.busy || !account.value) return
    const owner = account.value
    const epoch = generation
    const owns = () => active(owner, epoch) && states[block.id] === state
    state.busy = true
    try {
      const fresh = await fetchQuestion(block)
      if (!owns()) return
      const latest = options.blocks().find((b) => b.id === block.id)
      if (latest && answerVersion(fresh) < answerVersion(latest)) {
        state.fresh = false
        state.error = t('ask.flow.refreshError')
        return
      }
      identities.set(block.id, `${questionIdentity(fresh)}:${answerVersion(fresh)}`)
      const sameVersion =
        answerVersion(fresh) === answerVersion(block) && questionIdentity(fresh) === questionIdentity(block)
      const next = sameVersion ? state : hydrate(fresh)
      if (acknowledgeAsk(localStorage, owner, fresh, viewer.value)) {
        next.pending = null
        next.editing = false
        next.draft = emptyAskDraft()
        next.saved = false
      }
      next.fresh = true
      next.error = null
      next.busy = false
      if (
        next.pending &&
        !submittedAnswer(fresh, next.pending) &&
        (next.pending.question !== questionIdentity(fresh) ||
          next.pending.payload.expect_version !== answerVersion(fresh))
      ) {
        next.conflict = true
        next.error = t(
          answerVersion(fresh) === next.pending.payload.expect_version
            ? 'ask.flow.questionChanged'
            : 'ask.flow.conflict'
        )
      }
      states[block.id] = next
      options.replace(fresh)
    } catch (error) {
      if (owns()) state.error = error instanceof Error ? error.message : t('ask.flow.refreshError')
    } finally {
      if (owns()) state.busy = false
    }
  }

  async function submit(block: Block): Promise<void> {
    const state = states[block.id]
    if (
      !state ||
      state.busy ||
      !state.fresh ||
      state.conflict ||
      state.storageBlocked ||
      !canAnswer(block, viewer.value) ||
      account.value !== myId()
    )
      return
    // Group members are only written by the atomic group adapter, never a loop
    // through this endpoint. The group controller owns their submit action.
    if (block.meta?.ask_group) return
    if (!state.pending && !validAskDraft(block, state.draft)) {
      state.error = t('ask.group.invalid')
      return
    }
    const owner = account.value
    const author = viewer.value
    const epoch = generation
    const owns = () => active(owner, epoch) && states[block.id] === state
    try {
      state.pending = prepareAskSubmission(localStorage, owner, block, state.draft)
    } catch {
      state.storageBlocked = true
      state.error = t('ask.flow.storageError')
      return
    }
    state.busy = true
    const request = Symbol(block.id)
    sending.set(block.id, request)
    state.error = null
    try {
      const updated = await answerOptions(block.id, state.pending.payload, author)
      if (!owns()) return
      if (!acknowledgeAsk(localStorage, owner, updated, author)) throw new Error(t('ask.flow.unconfirmed'))
      state.pending = null
      state.editing = false
      state.saved = false
      state.draft = emptyAskDraft()
      const latest = options.blocks().find((b) => b.id === block.id)
      if (!latest || answerVersion(updated) >= answerVersion(latest)) options.replace(updated)
    } catch (error) {
      if (!owns()) return
      state.error = error instanceof Error ? error.message : t('ask.flow.unconfirmed')
      // Never discard an unknown operation on timeout, 5xx, authentication loss,
      // or even 409 alone. Refresh proves whether its expected version advanced.
      if (error instanceof ApiError && error.status === 409) state.fresh = false
    } finally {
      if (sending.get(block.id) === request) sending.delete(block.id)
      if (owns()) state.busy = false
    }
  }

  function action(block: Block, action: AskAction): void {
    const state = states[block.id]
    if (!state || account.value !== myId()) return
    if (action.type === 'refresh') {
      void refresh(block)
      return
    }
    if (!canAnswer(block, viewer.value) || state.busy) return
    if (action.type === 'submit') {
      void submit(block)
      return
    }
    if (action.type === 'resolve-conflict') {
      if (!state.conflict || !canRevisePending(block, state.pending, state.fresh)) return
      // A fresh higher version without this operation proves the old expected
      // version can no longer commit. Preserve its contents as an explicit edit.
      try {
        localStorage.removeItem(askPendingKey(account.value, block))
      } catch {
        state.error = t('ask.flow.storageError')
        return
      }
      state.pending = null
      state.conflict = false
      state.error = null
      state.editing = true
    }
    if (state.pending || state.storageBlocked) return
    if (action.type === 'correct') {
      const answer = block.meta?.answer_log?.at(-1)
      if (answer) state.draft = draftFromAnswer(answer)
      state.editing = true
    } else if (action.type === 'cancel') {
      state.editing = false
      state.draft = emptyAskDraft()
      state.saved = false
      try {
        localStorage.removeItem(askDraftKey(account.value, block))
      } catch {
        state.error = t('ask.flow.draftError')
      }
    } else if (action.type === 'draft') {
      state.draft = { ...action.draft }
      state.editing = true
      try {
        saveAskDraft(localStorage, account.value, block, state.draft)
        state.saved = true
        state.error = null
      } catch {
        state.saved = false
        state.error = t('ask.flow.draftError')
      }
    }
  }

  return { askStates: states, askAction: action, askViewer: viewer, askAccount: account }
}
