// 写到一半的评论：每处选中的字一份草稿，换了话题、关了评论栏都还在，会话里存一份。
// 选中的字记成跟着正文走的位置（lib/docCommentSpots）；那份位置活不过页面刷新，刷新
// 后回来的草稿按字找回原处。
import type { CommentSpot } from '../lib/docCommentSpots'

import { computed, effectScope, markRaw, onScopeDispose, reactive, watch } from 'vue'

import { t } from '@/i18n'

/** 发出一条评论；评的是 `spot` 那几个字（整篇时 quote 是空的）。 */
export type SendDocComment = (topicId: string, content: string, spot: CommentSpot) => Promise<unknown>

type Target = CommentSpot
type Draft = { target: Target; text: string; revision: number; sending: boolean; error: string | null }
type TopicDrafts = { active: string | null; hidden: boolean; drafts: Record<string, Draft> }

// Shared records survive pane unmounts and keep late receipts tied to their draft.
const cache = new Map<string, TopicDrafts>()

function topicDrafts(topicId: string, author: string): TopicDrafts {
  const key = `cheese:doc-comments:${JSON.stringify([author, topicId])}`
  const existing = cache.get(key)
  if (existing) return existing
  let initial: TopicDrafts = { active: null, hidden: false, drafts: {} }
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (saved && typeof saved.drafts === 'object' && saved.drafts !== null) {
      for (const [id, value] of Object.entries(saved.drafts)) {
        const item = value as Partial<Draft>
        const target = item.target
        if (typeof item.text !== 'string' || typeof target?.quote !== 'string') continue
        if (typeof target.from !== 'number' || typeof target.to !== 'number') continue
        initial.drafts[id] = {
          target: { quote: target.quote, from: target.from, to: target.to, rel: null },
          text: item.text,
          revision: 0,
          sending: false,
          error: null,
        }
      }
      if (typeof saved.active === 'string' && initial.drafts[saved.active]) initial.active = saved.active
      initial.hidden = saved.hidden === true
    }
  } catch {
    // A storage failure must not prevent commenting or discard the in-memory draft.
  }
  initial = reactive(initial)
  cache.set(key, initial)
  effectScope(true).run(() =>
    watch(
      initial,
      () => {
        try {
          const drafts = Object.fromEntries(
            Object.entries(initial.drafts).map(([id, d]) => [
              id,
              { target: { quote: d.target.quote, from: d.target.from, to: d.target.to }, text: d.text },
            ])
          )
          sessionStorage.setItem(key, JSON.stringify({ active: initial.active, hidden: initial.hidden, drafts }))
        } catch {
          /* Keep the draft in memory when storage is unavailable. */
        }
      },
      { deep: true, flush: 'sync' }
    )
  )
  return initial
}

export function useDocCommentDraft(
  topicId: () => string | null,
  author: () => string,
  send: SendDocComment,
  posted: () => void
) {
  let disposed = false
  onScopeDispose(() => {
    disposed = true
  })
  const state = computed(() => (topicId() ? topicDrafts(topicId()!, author()) : null))
  const current = computed(() => {
    const s = state.value
    return s?.active ? s.drafts[s.active] ?? null : null
  })
  const draft = computed(() => (state.value?.hidden ? null : current.value?.target ?? null))
  const hasDraft = computed(() => !!current.value)
  const busy = computed(() => Object.values(state.value?.drafts ?? {}).some((entry) => entry.sending))
  const text = computed({
    get: () => current.value?.text ?? '',
    set: (value: string) => {
      if (!current.value) return
      current.value.text = value
      current.value.revision++
    },
  })
  const sending = computed(() => current.value?.sending ?? false)
  const errorMsg = computed(() => current.value?.error ?? null)

  /** 开一段评论草稿。`prefill` 只写进还空着的草稿：同一处已经写了的字不被换掉。 */
  function open(target: Target, prefill?: string) {
    const s = state.value
    if (!s) return
    const key = JSON.stringify([target.from, target.quote])
    // The shared-document positions stay plain objects: Yjs reads them, Vue need not watch them.
    const spot = { ...target, rel: target.rel ? markRaw(target.rel) : null }
    s.drafts[key] ??= { target: spot, text: '', revision: 0, sending: false, error: null }
    if (prefill && !s.drafts[key].text.trim()) s.drafts[key].text = prefill
    s.active = key
    s.hidden = false
  }
  function close() {
    const s = state.value
    if (!s || busy.value) return false
    s.hidden = true
    return true
  }
  function cancel() {
    const s = state.value
    if (!s || !s.active || busy.value) return
    delete s.drafts[s.active]
    s.active = null
  }
  async function submit() {
    const tid = topicId(),
      s = state.value,
      entry = current.value
    const body = entry?.text.trim()
    if (!tid || !s || !entry || !body || entry.sending) return
    const key = s.active!,
      revision = entry.revision,
      sender = author()
    entry.sending = true
    entry.error = null
    try {
      await send(tid, body, entry.target)
      // A receipt may arrive after switching topic/selection or typing a new draft.
      if (entry.revision === revision) {
        delete s.drafts[key]
        if (s.active === key) s.active = null
      }
      if (!disposed && topicId() === tid && author() === sender) posted()
    } catch (e) {
      entry.error = e instanceof Error ? e.message : t('work.room.comments.postFailed')
    } finally {
      entry.sending = false
    }
  }
  return {
    draft,
    hasDraft,
    target: computed(() => current.value?.target ?? null),
    text,
    sending,
    busy,
    errorMsg,
    open,
    close,
    cancel,
    submit,
  }
}
