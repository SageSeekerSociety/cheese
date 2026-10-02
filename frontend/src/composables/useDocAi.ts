import type { DocSelectionSnapshot } from '../lib/docAiSelection'
import type {
  DocAiAccept,
  DocAiCard,
  DocAiDisplayContext,
  DocAiInput,
  DocAiSelection,
  DocAiSource,
} from '../lib/docAiTypes'

import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { ApiError } from '../api'
import {
  acceptDocAiProposal,
  cancelDocAiRequest,
  createDocAiRequest,
  getDocAiProposal,
  getDocAiRequest,
  getDocAiSource,
  listDocAiRequests,
} from '../api/docAi'
import { t } from '../i18n'
import { verifyDocAiFrozenContext, verifyDocAiPreparedContext } from '../lib/docAiFrozenContext'
import { validateDocSelection } from '../lib/docAiSelection'
import { myId } from '../me'

interface Context {
  topic: () => string | null
  raw: () => string
  prefix: () => string
  version: () => number
  blocked: () => boolean
  reload: (topic: string) => Promise<void>
}
type Operation = { kind: 'request'; body: DocAiInput } | { kind: 'accept'; proposal: string; body: DocAiAccept }

export function useDocAi(context: Context) {
  const opened = ref(false)
  const question = ref('')
  const error = ref('')
  const busy = ref(false)
  const cards = shallowRef<DocAiCard[]>([])
  const source = shallowRef<DocAiSource | null>(null)
  const selection = shallowRef<DocAiSelection | null>(null)
  const selectionStatus = ref('')
  const preparedContext = shallowRef<DocAiDisplayContext>({ state: 'unavailable' })
  const unknown = shallowRef<Operation | null>(null)
  let selectionRequest = 0
  let epoch = 0
  let disposed = false
  let restoring = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller = new AbortController()
  let actor = myId()
  let room = context.topic()
  const key = () => `cheese.doc-ai.v1:${actor}:${room}`
  const active = (generation: number) =>
    !disposed && generation === epoch && actor === myId() && room === context.topic()
  const clearTimer = () => {
    if (timer) clearTimeout(timer)
    timer = undefined
  }
  function persist(operation: Operation | null) {
    // Failure to persist means do not send. Never silently lose an unknown write.
    if (operation) localStorage.setItem(`${key()}:operation`, JSON.stringify(operation))
    else localStorage.removeItem(`${key()}:operation`)
    unknown.value = operation
  }
  function message(cause: unknown) {
    return cause instanceof Error ? cause.message : t('work.room.docAi.failed')
  }
  function definitelyRejected(cause: unknown) {
    return cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408
  }
  async function refresh() {
    const generation = epoch
    const topic = room
    if (!topic || !actor || !active(generation)) return
    try {
      const list = await listDocAiRequests(topic, controller.signal)
      const rows = await Promise.all(
        list.requests.map(async (row) => {
          const request = await getDocAiRequest(topic, row.request_id, controller.signal)
          const proposal = request.proposal_id
            ? await getDocAiProposal(topic, request.proposal_id, controller.signal)
            : undefined
          const context = await verifyDocAiFrozenContext(request, proposal)
          return { request, proposal, context }
        })
      )
      if (!active(generation)) return
      cards.value = rows
      clearTimer()
      if (rows.some(({ request }) => request.state === 'pending' || request.state === 'running')) {
        timer = setTimeout(() => {
          void refresh()
        }, 1800)
      }
    } catch (cause) {
      if (active(generation)) error.value = message(cause)
    }
  }
  async function prepare(snapshot: DocSelectionSnapshot | null) {
    opened.value = true
    selection.value = null
    source.value = null
    selectionStatus.value = ''
    preparedContext.value = { state: 'unavailable' }
    const generation = epoch
    const request = ++selectionRequest
    const current = () => active(generation) && request === selectionRequest
    const version = context.version()
    const raw = context.raw()
    if (!room || !actor) {
      error.value = t('work.room.docAi.signIn')
      return
    }
    try {
      const canonical = await getDocAiSource(room, controller.signal)
      if (!current() || version !== context.version() || raw !== context.raw()) return
      source.value = canonical
      if (snapshot && !context.blocked()) {
        const span = await validateDocSelection(snapshot, canonical, raw, version, context.prefix())
        if (!current() || version !== context.version() || raw !== context.raw() || context.blocked()) return
        selection.value = span
      }
      if (snapshot && !selection.value) selectionStatus.value = t('work.room.docAi.unverified')
      if (
        !context.blocked() &&
        canonical.base_version === version &&
        canonical.source === raw &&
        (!snapshot || selection.value)
      ) {
        const display = await verifyDocAiPreparedContext(canonical, selection.value)
        if (!current() || version !== context.version() || raw !== context.raw() || context.blocked()) return
        preparedContext.value = display
      }
      await refresh()
    } catch (cause) {
      if (current()) error.value = message(cause)
    }
  }
  async function execute(operation: Operation) {
    if (busy.value || !room || !actor) return
    const generation = epoch
    const topic = room
    if (!active(generation)) return
    const replayingUnknown = unknown.value !== null
    busy.value = true
    error.value = ''
    try {
      persist(operation)
      if (operation.kind === 'request') await createDocAiRequest(topic, operation.body)
      else await acceptDocAiProposal(topic, operation.proposal, operation.body)
      if (!active(generation)) return
      persist(null)
      if (operation.kind === 'request') {
        // A later edit to the input survives the receipt.
        if (question.value === operation.body.question) question.value = ''
      } else {
        // The receipt may predate later edits. Only canonical GET feeds the editor;
        // its existing reconciliation preserves any typing since acceptance began.
        await context.reload(topic)
      }
      await refresh()
    } catch (cause) {
      if (!active(generation)) return
      error.value = message(cause)
      // A replay rejected before receipt lookup proves nothing about the first
      // attempt. Retain the frozen identity until a successful receipt arrives.
      if (!replayingUnknown && definitelyRejected(cause)) persist(null)
    } finally {
      if (active(generation)) busy.value = false
    }
  }
  async function submit(kind: 'ask' | 'propose') {
    if (unknown.value || busy.value || !question.value.trim() || !room || !actor) return
    const canonical = source.value
    if (
      !canonical ||
      context.blocked() ||
      canonical.base_version !== context.version() ||
      canonical.source !== context.raw()
    ) {
      error.value = t('work.room.docAi.unverified')
      return
    }
    if (kind === 'propose' && !selection.value) {
      error.value = t('work.room.docAi.unverified')
      return
    }
    await execute({
      kind: 'request',
      body: {
        operation_id: crypto.randomUUID(),
        kind,
        question: question.value,
        document_id: canonical.document_id,
        base_version: canonical.base_version,
        ...(selection.value ? { selection: selection.value } : {}),
      },
    })
  }
  async function accept(id: string) {
    const proposal = cards.value.find((card) => card.proposal?.proposal_id === id)?.proposal
    if (!proposal || unknown.value || busy.value || context.blocked() || proposal.base_version !== context.version()) {
      error.value = t('work.room.docAi.unverified')
      return
    }
    await execute({
      kind: 'accept',
      proposal: id,
      body: {
        operation_id: crypto.randomUUID(),
        expected_version: proposal.base_version,
        revision: proposal.revision,
      },
    })
  }
  async function recover() {
    // Replaying the frozen payload is allowed even when the current doc moved.
    // Server journal checks the operation before it checks the now-stale version.
    if (unknown.value) await execute(unknown.value)
    else await refresh()
  }
  async function cancel(id: string) {
    const generation = epoch
    if (!room || busy.value || !active(generation)) return
    busy.value = true
    try {
      await cancelDocAiRequest(room, id)
      if (active(generation)) await refresh()
    } catch (cause) {
      if (active(generation)) error.value = message(cause)
    } finally {
      if (active(generation)) busy.value = false
    }
  }
  function reset() {
    epoch++
    controller.abort()
    controller = new AbortController()
    clearTimer()
    actor = myId()
    room = context.topic()
    busy.value = false
    cards.value = []
    source.value = null
    selection.value = null
    preparedContext.value = { state: 'unavailable' }
    restoring = true
    question.value = ''
    error.value = ''
    unknown.value = null
    try {
      question.value = localStorage.getItem(`${key()}:draft`) ?? ''
      const saved = localStorage.getItem(`${key()}:operation`)
      if (saved) unknown.value = JSON.parse(saved) as Operation
    } catch (cause) {
      error.value = message(cause)
    }
    restoring = false
    void refresh()
  }
  watch(context.topic, reset, { immediate: true, flush: 'sync' })
  watch(
    question,
    (value) => {
      if (restoring || actor !== myId()) return
      try {
        localStorage.setItem(`${key()}:draft`, value)
      } catch (cause) {
        error.value = message(cause)
      }
    },
    { flush: 'sync' }
  )
  // Sign-in can change in the same SPA. Every async completion and action also
  // checks identity; this timer only removes the old actor's visible cards.
  const identityTimer = setInterval(() => {
    if (actor !== myId()) reset()
  }, 500)
  onBeforeUnmount(() => {
    disposed = true
    epoch++
    clearTimer()
    clearInterval(identityTimer)
    controller.abort()
  })
  return {
    opened,
    question,
    error,
    busy,
    cards,
    source,
    selection,
    selectionStatus,
    preparedContext,
    unknown,
    prepare,
    submit,
    accept,
    recover,
    refresh,
    cancel,
  }
}
