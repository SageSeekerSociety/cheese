import type { Block } from '../cx_types'
import type { AskGroupData, AskGroupScope } from '../lib/askGroup'
import type { AskGroupAction, AskGroupState } from '../lib/askGroupState'
import type { AskDraft } from '../lib/askState'

import { onScopeDispose, reactive, watch } from 'vue'

import { ApiError } from '../api'
import { t } from '../i18n'
import { groupKey, groupOf, makeGroupSubmission } from '../lib/askGroup'
import { groupAcknowledged, groupPendingKey, loadGroupPending } from '../lib/askGroupState'
import {
  answerVersion,
  askDraftKey,
  canAnswer,
  draftFromAnswer,
  emptyAskDraft,
  loadAskDraft,
  questionIdentity,
  saveAskDraft,
  validAskDraft,
} from '../lib/askState'
import { myId } from '../me'
import { readAskGroup, settleAskGroup } from '../services/askGroups'

export function useAskGroups(options: {
  blocks: () => Block[]
  account: () => string
  viewer: () => string
  replace: (block: Block) => void
}) {
  const groups = reactive<Record<string, AskGroupState>>({})
  let epoch = 0
  let stopped = false
  const identities = new Map<string, string>()
  const reads = new Map<string, symbol>()
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  onScopeDispose(() => {
    stopped = true
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
  })
  function schedule(state: AskGroupState) {
    const key = groupKey(state.scope)
    clearTimeout(timers.get(key))
    if (stopped || !state.data?.settlement || state.data.receipt?.completed_at || state.unavailable) return
    timers.set(
      key,
      setTimeout(() => {
        timers.delete(key)
        if (document.visibilityState !== 'hidden' && options.blocks().some((b) => state.scope.members.includes(b.id)))
          void refresh(state.scope, true)
        else schedule(state)
      }, 5000)
    )
  }
  const current = (owner: string, generation: number) =>
    owner === myId() && owner === options.account() && generation === epoch && !stopped

  watch(
    options.account,
    () => {
      epoch++
      identities.clear()
      reads.clear()
      for (const timer of timers.values()) clearTimeout(timer)
      timers.clear()
      for (const key of Object.keys(groups)) delete groups[key]
    },
    { flush: 'sync' }
  )

  watch(
    () => [options.account(), options.blocks()] as const,
    () => {
      for (const block of options.blocks()) {
        const scope = groupOf(block)
        if (!scope || !options.account()) continue
        const key = groupKey(scope)
        if (!groups[key]) {
          const state: AskGroupState = {
            scope,
            anchor: block.id,
            data: null,
            forms: {},
            pending: null,
            busy: false,
            fresh: false,
            error: null,
            storageBlocked: false,
            confirm: false,
            conflict: false,
            unavailable: false,
          }
          try {
            state.pending = loadGroupPending(localStorage, options.account(), scope)
          } catch {
            state.storageBlocked = true
            state.error = t('ask.flow.storageError')
          }
          groups[key] = state
          void refresh(scope)
        } else if (!options.blocks().some((b) => b.id === groups[key]!.anchor)) {
          groups[key]!.anchor = block.id
        }
        const identity = JSON.stringify([block.meta?.answer_log, block.meta?.group_settle])
        const changed = identities.has(block.id) && identities.get(block.id) !== identity
        identities.set(block.id, identity)
        if (changed) void refresh(scope, true)
      }
    },
    { immediate: true, deep: true }
  )

  function adopt(state: AskGroupState, data: AskGroupData): void {
    const owner = options.account()
    const ack = state.pending && groupAcknowledged(data, state.pending, options.viewer())
    if (ack && state.pending) {
      for (const item of state.pending.payload.answered) {
        const block = data.blocks.find((b) => b.id === item.block_id)!
        localStorage.removeItem(askDraftKey(owner, block, item.expect_version))
      }
      localStorage.removeItem(groupPendingKey(owner, state.scope))
      state.pending = null
    }
    const previous = state.data
    state.data = data
    state.fresh = true
    state.conflict = !!state.pending && (data.settlement?.v ?? 0) !== state.pending.payload.expect_version && !ack
    state.unavailable = false
    for (const block of data.blocks) {
      const oldBlock = previous?.blocks.find((b) => b.id === block.id)
      const oldForm = state.forms[block.id]
      const keep =
        !ack &&
        oldBlock &&
        oldForm &&
        answerVersion(oldBlock) === answerVersion(block) &&
        questionIdentity(oldBlock) === questionIdentity(block)
      let draft = keep ? oldForm.draft : emptyAskDraft()
      try {
        if (!keep) draft = loadAskDraft(localStorage, owner, block) ?? draft
      } catch {
        state.storageBlocked = true
        state.error = t('ask.flow.storageError')
      }
      const pendingAnswer = state.pending?.payload.answered.find((a) => a.block_id === block.id)
      if (!keep && !state.pending && !draft.kind && !draft.note && data.settlement?.later.includes(block.id))
        draft.later = true
      if (pendingAnswer)
        draft = {
          kind: pendingAnswer.kind,
          option: pendingAnswer.option ?? '',
          note: pendingAnswer.note ?? '',
          later: false,
        }
      else if (state.pending?.payload.later.some((a) => a.block_id === block.id)) draft.later = true
      state.forms[block.id] = {
        draft,
        pending: null,
        editing: keep ? oldForm.editing : !!draft.kind || !!draft.note,
        busy: !!state.pending,
        fresh: true,
        saved: keep ? oldForm.saved : !!draft.kind || draft.later,
        error: keep ? oldForm.error : null,
        conflict: false,
        storageBlocked: state.storageBlocked,
      }
      identities.set(block.id, JSON.stringify([block.meta?.answer_log, block.meta?.group_settle]))
      options.replace(block)
    }
  }

  async function refresh(scope: AskGroupScope, quiet = false): Promise<void> {
    const state = groups[groupKey(scope)]
    const key = groupKey(scope)
    if (!state || state.busy || reads.has(key)) return
    const request = Symbol(key)
    reads.set(key, request)
    const owner = options.account(),
      generation = epoch
    const owns = () => current(owner, generation) && groups[key] === state && reads.get(key) === request
    if (!quiet) {
      state.busy = true
      for (const form of Object.values(state.forms)) form.busy = true
      state.error = null
    }
    try {
      if (state.storageBlocked && !quiet) {
        const pending = loadGroupPending(localStorage, owner, scope)
        state.pending = pending
        state.storageBlocked = false
      }
      const data = await readAskGroup(scope)
      if (!owns() || (quiet && state.busy)) return
      const latestBlocks = [...options.blocks(), ...(state.data?.blocks ?? [])]
      if (
        data.blocks.some((b) =>
          latestBlocks.some((latest) => latest.id === b.id && answerVersion(latest) > answerVersion(b))
        ) ||
        (state.data?.settlement?.v ?? 0) > (data.settlement?.v ?? 0)
      ) {
        if (!quiet) {
          state.fresh = false
          state.error = t('ask.flow.refreshError')
        }
        return
      }
      adopt(state, data)
    } catch (error) {
      if (!owns() || (quiet && state.busy)) return
      state.unavailable = error instanceof ApiError && [404, 501].includes(error.status)
      state.error = state.unavailable
        ? t('ask.group.unavailable')
        : error instanceof Error
          ? error.message
          : t('ask.flow.refreshError')
    } finally {
      const owned = owns()
      if (reads.get(key) === request) reads.delete(key)
      if (owned) {
        if (!quiet) {
          state.busy = false
          for (const form of Object.values(state.forms)) form.busy = !!state.pending
        }
        schedule(state)
      }
    }
  }

  async function submit(state: AskGroupState, confirmed: boolean): Promise<void> {
    if (!state.data || !state.fresh || state.busy || state.storageBlocked || (state.conflict && !state.pending)) return
    const owner = options.account(),
      generation = epoch
    const drafts: Record<string, AskDraft | undefined> = {}
    for (const block of state.data.blocks) {
      const form = state.forms[block.id]
      if (
        !state.pending &&
        form?.editing &&
        !form.draft.later &&
        ((form.draft.kind && !validAskDraft(block, form.draft)) || (!form.draft.kind && form.draft.note.trim()))
      ) {
        state.error = t('ask.group.invalid')
        return
      }
      if (form?.editing || form?.draft.later) drafts[block.id] = form.draft
    }
    try {
      const payload = state.pending?.payload ?? makeGroupSubmission(state.data, drafts)
      if (
        !state.pending &&
        !confirmed &&
        payload.unanswered.some((i) => !state.data!.blocks.find((b) => b.id === i.block_id)?.meta?.answer_log?.length)
      ) {
        state.confirm = true
        return
      }
      for (const item of payload.answered) {
        const block = state.data.blocks.find((b) => b.id === item.block_id)!
        if (!canAnswer(block, options.viewer())) return
      }
      if (!state.pending) {
        const pending = { account: owner, scope: groupKey(state.scope), payload }
        localStorage.setItem(groupPendingKey(owner, state.scope), JSON.stringify(pending))
        state.pending = pending
      }
    } catch {
      state.error = t('ask.flow.storageError')
      state.storageBlocked = true
      return
    }
    reads.delete(groupKey(state.scope))
    state.busy = true
    state.confirm = false
    state.error = null
    for (const form of Object.values(state.forms)) form.busy = true
    try {
      const data = await settleAskGroup(state.scope, state.pending!.payload)
      if (!current(owner, generation)) return
      if (!groupAcknowledged(data, state.pending!, options.viewer())) throw new Error(t('ask.flow.unconfirmed'))
      adopt(state, data)
    } catch (error) {
      if (!current(owner, generation)) return
      state.error = error instanceof Error ? error.message : t('ask.flow.unconfirmed')
      if (error instanceof ApiError && error.status === 409) state.fresh = false
    } finally {
      if (current(owner, generation)) {
        state.busy = false
        for (const form of Object.values(state.forms)) form.busy = !!state.pending
        schedule(state)
      }
    }
  }

  function action(scope: AskGroupScope, action: AskGroupAction): void {
    const state = groups[groupKey(scope)]
    if (!state || !options.account() || options.account() !== myId()) return
    if (action.type === 'refresh' || (action.type === 'question' && action.action.type === 'refresh')) {
      void refresh(scope)
      return
    }
    if (!state.data || state.busy) return
    if (action.type === 'back') {
      state.confirm = false
      return
    }
    if (action.type === 'submit' || action.type === 'confirm') {
      void submit(state, action.type === 'confirm')
      return
    }
    if (state.pending || state.storageBlocked) return
    if (action.type === 'question' || action.type === 'later') {
      const block = state.data.blocks.find((b) => b.id === action.blockId)
      const form = state.forms[action.blockId]
      if (!block || !form || !canAnswer(block, options.viewer())) return
      if (action.type === 'later') form.draft = { ...form.draft, later: !form.draft.later }
      else if (action.action.type === 'draft') {
        form.draft = action.action.draft
        form.editing = true
      } else if (action.action.type === 'correct') {
        const answer = block.meta?.answer_log?.at(-1)
        if (answer) form.draft = draftFromAnswer(answer)
        form.editing = true
      } else if (action.action.type === 'cancel') {
        form.editing = false
        form.draft = emptyAskDraft()
        form.saved = false
        try {
          localStorage.removeItem(askDraftKey(options.account(), block))
        } catch {
          form.error = t('ask.flow.draftError')
        }
        return
      }
      try {
        saveAskDraft(localStorage, options.account(), block, form.draft)
        form.saved = true
      } catch {
        form.saved = false
        form.error = t('ask.flow.draftError')
      }
    }
  }

  return { askGroups: groups, askGroupAction: action }
}
