import { computed, effectScope, onScopeDispose, reactive, watch } from 'vue'

export type SendDocComment = (topicId: string, content: string, anchor?: string, quote?: string) => Promise<unknown>

import { t } from '@/i18n'

type Target = { anchorId: string | null; quote: string }
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
        if (typeof item.text !== 'string' || typeof item.target?.quote !== 'string') continue
        if (item.target.anchorId !== null && typeof item.target.anchorId !== 'string') continue
        initial.drafts[id] = { target: item.target, text: item.text, revision: 0, sending: false, error: null }
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
            Object.entries(initial.drafts).map(([id, d]) => [id, { target: d.target, text: d.text }])
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

  function open(target: Target) {
    const s = state.value
    if (!s) return
    const key = JSON.stringify([target.anchorId, target.quote])
    s.drafts[key] ??= { target: { ...target }, text: '', revision: 0, sending: false, error: null }
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
      await send(tid, body, entry.target.anchorId ?? undefined, entry.target.quote)
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
