import type { Block } from '../cx_types'
import type { AskGroupData, AskGroupScope } from '../lib/askGroup'
import type { AskGroupAction, AskGroupState } from '../lib/askGroupState'
import type { AskDraft } from '../lib/askState'

import { onScopeDispose, reactive, watch } from 'vue'

import { ApiError } from '../api'
import { t } from '../i18n'
import { groupKey, groupOf, makeGroupSubmission } from '../lib/askGroup'
import { groupAcknowledged, groupPendingKey, groupQuestionChanged, loadGroupPending } from '../lib/askGroupState'
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
import { renderNoticeMessage } from '../lib/noticeText'
import { myId } from '../me'
import { listAwaitingAskGroups, readAskGroup, settleAskGroup } from '../services/askGroups'

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
  const questionRevisions = new Map<string, number>()
  const identityOf = (block: Block) =>
    JSON.stringify([questionIdentity(block), block.meta?.answer_log, block.meta?.group_settle])
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

  // 登记一组：建状态、读回它的题。两个入口共用 —— 从时间线里冒出来的成员块，
  // 以及进房间时 `openRoom` 一次问来的「我还欠哪些组」。anchor 只是个锚点，不必
  // 真的在时间线里，所以没加载到的组也登记得起来。
  function ensure(scope: AskGroupScope, anchor: string): void {
    const key = groupKey(scope)
    if (groups[key]) return
    const state: AskGroupState = {
      scope,
      anchor,
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
  }

  // 进房间时问一次：这一间里我还欠哪些组的回答。时间线默认只加载最近一屏，
  // 早先发的组可能根本不在那一屏里 —— 上面那个块驱动的登记也就轮不到它。这个
  // 读接口按登记字段（id/asked_by/members/锚点）把组交回来，于是面板第一帧就接管。
  async function openRoom(topicId: string): Promise<void> {
    if (stopped || !topicId || !options.account()) return
    let scopes: Awaited<ReturnType<typeof listAwaitingAskGroups>>
    try {
      scopes = await listAwaitingAskGroups(topicId)
    } catch {
      return // 读失败不是错误：块驱动的登记照旧，翻到那条仍能接管
    }
    if (stopped) return
    for (const scope of scopes) if (scope.topic_id === topicId) ensure(scope, scope.anchor)
  }

  watch(
    options.account,
    () => {
      epoch++
      identities.clear()
      questionRevisions.clear()
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
          ensure(scope, block.id)
        } else if (!options.blocks().some((b) => b.id === groups[key]!.anchor)) {
          groups[key]!.anchor = block.id
        }
        const identity = identityOf(block)
        const changed = identities.has(block.id) && identities.get(block.id) !== identity
        identities.set(block.id, identity)
        if (changed) {
          questionRevisions.set(block.id, (questionRevisions.get(block.id) ?? 0) + 1)
          const state = groups[key]!
          if (state.data) {
            const live = {
              ...state.data,
              blocks: state.data.blocks.map((b) =>
                b.id === block.id && answerVersion(block) >= answerVersion(b) ? block : b
              ),
            }
            const settle = block.meta?.group_settle as AskGroupData['settlement']
            if (settle && settle.v > (live.settlement?.v ?? 0)) {
              live.settlement = settle
              live.receipt = null
            }
            adopt(state, live, false)
          }
          void refresh(scope, true)
        }
      }
    },
    { immediate: true, deep: true }
  )

  function acknowledge(state: AskGroupState, data: AskGroupData): Set<string> {
    const cleared = new Set<string>()
    const pending = state.pending
    if (!pending || !groupAcknowledged(data, pending, options.viewer())) return cleared
    state.confirmedOperation = { settlement: data.settlement, receipt: data.receipt }
    for (const item of pending.payload.answered) {
      const matches = (draft: AskDraft | undefined) =>
        draft &&
        !draft.later &&
        draft.kind === item.kind &&
        (draft.option || '') === (item.option ?? '') &&
        draft.note === (item.note ?? '')
      const block = data.blocks.find((b) => b.id === item.block_id)!
      const key = askDraftKey(options.account(), block, item.expect_version)
      try {
        const stored = localStorage.getItem(key)
        if (stored) {
          const saved = JSON.parse(stored)
          if (saved.question === pending.questions?.[item.block_id] && matches(saved.draft))
            localStorage.removeItem(key)
        }
      } catch {
        state.storageBlocked = true
        state.error = t('ask.group.cleanupError')
      }
      if (
        state.data?.blocks.some(
          (b) =>
            b.id === item.block_id &&
            answerVersion(b) === item.expect_version &&
            questionIdentity(b) === pending.questions?.[item.block_id]
        ) &&
        matches(state.forms[item.block_id]?.draft)
      )
        cleared.add(item.block_id)
    }
    try {
      localStorage.removeItem(groupPendingKey(options.account(), state.scope))
    } catch {
      state.storageBlocked = true
      state.error = t('ask.group.cleanupError')
    }
    state.pending = null
    state.conflict = false
    state.rejectedOperation = undefined
    return cleared
  }

  // A replay confirms its own operation, not that its snapshot is still current.
  // Merge against both the group reader and live timeline before rendering it.
  function visibleData(state: AskGroupState, incoming: AskGroupData, revisions?: Map<string, number>): AskGroupData {
    const known = [...(state.data?.blocks ?? []), ...options.blocks()]
    const blocks = incoming.blocks.map((block) =>
      known.reduce(
        (latest, candidate) =>
          candidate.id === block.id &&
          candidate.topic_id === block.topic_id &&
          (answerVersion(candidate) > answerVersion(latest) ||
            (answerVersion(candidate) === answerVersion(latest) &&
              revisions &&
              (questionRevisions.get(candidate.id) ?? 0) > (revisions.get(candidate.id) ?? 0)))
            ? candidate
            : latest,
        block
      )
    )
    let settlement = incoming.settlement
    const candidates = [
      state.data?.settlement,
      ...[...known, ...incoming.blocks]
        .filter((b) => {
          const scope = groupOf(b)
          return scope && groupKey(scope) === groupKey(state.scope)
        })
        .map((b) => b.meta?.group_settle as AskGroupData['settlement']),
    ]
    for (const candidate of candidates) {
      if (candidate && Number.isInteger(candidate.v) && candidate.v > (settlement?.v ?? 0)) settlement = candidate
    }
    const receipt =
      [incoming.receipt, state.data?.receipt].find((r) => r && r.event_id === settlement?.delivery_event_id) ?? null
    return { ...incoming, blocks, settlement, receipt }
  }

  function adopt(
    state: AskGroupState,
    incoming: AskGroupData,
    authoritative = true,
    revisions?: Map<string, number>
  ): void {
    const owner = options.account()
    const confirmed = authoritative && !!state.pending && groupAcknowledged(incoming, state.pending, options.viewer())
    const cleared = authoritative ? acknowledge(state, incoming) : new Set<string>()
    const data = visibleData(state, incoming, revisions)
    const previous = state.data
    state.data = data
    state.fresh =
      authoritative &&
      data.settlement?.v === incoming.settlement?.v &&
      data.blocks.every(
        (b, i) =>
          answerVersion(b) === answerVersion(incoming.blocks[i]!) &&
          questionIdentity(b) === questionIdentity(incoming.blocks[i]!)
      )
    state.conflict = !!state.pending && (data.settlement?.v ?? 0) !== state.pending.payload.expect_version
    state.unavailable = false
    state.questionChanged = !!state.pending && groupQuestionChanged(data, state.pending)
    for (const block of data.blocks) {
      const oldBlock = previous?.blocks.find((b) => b.id === block.id)
      const oldForm = state.forms[block.id]
      const keep =
        !cleared.has(block.id) &&
        oldBlock &&
        oldForm &&
        (confirmed || answerVersion(oldBlock) === answerVersion(block)) &&
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
      if (pendingAnswer && !state.questionChanged && answerVersion(block) === pendingAnswer.expect_version)
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
      identities.set(block.id, identityOf(block))
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
      const revisions = new Map(questionRevisions)
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
      adopt(state, data, true, revisions)
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
    if (
      !state.data ||
      !state.fresh ||
      state.busy ||
      state.storageBlocked ||
      state.questionChanged ||
      state.rejectedOperation ||
      (state.conflict && !state.pending)
    )
      return
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
        const pending = {
          account: owner,
          scope: groupKey(state.scope),
          payload,
          questions: Object.fromEntries(state.data.blocks.map((b) => [b.id, questionIdentity(b)])),
        }
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
    const pending = state.pending!
    const revisions = new Map(questionRevisions)
    const owns = () => current(owner, generation) && groups[groupKey(state.scope)] === state
    try {
      const data = await settleAskGroup(state.scope, pending.payload)
      if (!owns()) return
      if (!groupAcknowledged(data, pending, options.viewer())) throw new Error(t('ask.flow.unconfirmed'))
      adopt(state, data, true, revisions)
    } catch (error) {
      if (!owns()) return
      state.error = error instanceof Error ? error.message : t('ask.flow.unconfirmed')
      if (error instanceof ApiError && error.status === 409) {
        state.fresh = false
        // The server checks historical operation IDs before this exact rejection.
        // Other 409s (including reused IDs) do not prove that the op is absent.
        // The refusal arrives already rendered from its catalog key into the
        // reader's language, so that sentence is what tells this 409 apart.
        const stale = renderNoticeMessage({ key: 'askGroupVersionStale' }, '')
        if (error.message === stale && state.pending === pending) state.rejectedOperation = pending.payload.client_op_id
      }
    } finally {
      if (owns()) {
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
    if (action.type === 'resolve-conflict') {
      if (!state.fresh || !state.pending || state.rejectedOperation !== state.pending.payload.client_op_id) return
      try {
        localStorage.removeItem(groupPendingKey(options.account(), scope))
      } catch {
        state.error = t('ask.flow.storageError')
        return
      }
      state.pending = null
      state.rejectedOperation = undefined
      state.questionChanged = false
      state.conflict = false
      state.error = null
      for (const form of Object.values(state.forms)) form.busy = false
      return
    }
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

  return { askGroups: groups, askGroupAction: action, openRoom }
}
