// A document's comment threads: read whole (every reply), kept current by what
// its live connection hears (lib/docCommentSignals), and written one operation
// at a time.
//
// A write is remembered (localStorage) until its receipt comes back. A page
// that loses the connection or reloads mid-write therefore knows that thread's
// last write may or may not have landed, and offers to send the same operation
// again; the server applies an operation id once.
import type {
  DocThread,
  DocThreadActions,
  DocThreadActivity,
  DocThreadState,
  DocThreadWrite,
} from '../lib/docThreadTypes'

import { onBeforeUnmount, reactive, watch } from 'vue'

import { ApiError } from '../api'
import { listDocThreads, stopDocThreadAgent, writeDocThread } from '../api/docThreads'
import { listenToComments } from '../lib/docCommentSignals'
import { myId } from '../me'

type Operation = { id: string; action: 'replies' | 'resolve' | 'reopen'; body: DocThreadWrite }
export function useDocThreads(document: () => string | null) {
  const state = reactive<DocThreadState>({ threads: [], activity: {}, errors: {}, busy: false, unknown: null })
  let doc = document()
  let actor = myId()
  let epoch = 0
  let reads = 0
  let disposed = false
  let operation: Operation | null = null
  let stopListening: (() => void) | null = null
  const key = () => `cheese.doc-thread.operation.v1:${actor}:${doc}`
  const active = (generation: number) => !disposed && generation === epoch && doc === document() && actor === myId()

  function install(thread: DocThread) {
    const at = state.threads.findIndex((t) => t.comment.id === thread.comment.id)
    if (at < 0) state.threads.push(thread)
    else if (thread.revision >= state.threads[at].revision) state.threads[at] = { ...state.threads[at], ...thread }
  }
  /** Read every thread again; what the agent is doing comes with them. */
  async function refresh() {
    const generation = epoch
    const sequence = ++reads
    if (!doc || !active(generation)) return
    try {
      const { data } = await listDocThreads(doc)
      if (!active(generation) || sequence !== reads) return
      state.threads = data
      const next: Record<string, DocThreadActivity> = {}
      for (const thread of data) {
        if (!thread.answering) continue
        const now = state.activity[thread.comment.id]
        next[thread.comment.id] = now?.state === thread.answering ? now : { state: thread.answering }
      }
      state.activity = next
    } catch {
      // The list on screen stays; the next signal reads it again.
    }
  }
  function persist(value: Operation | null) {
    if (value) localStorage.setItem(key(), JSON.stringify(value))
    else localStorage.removeItem(key())
    operation = value
    state.unknown = value?.id ?? null
  }
  async function execute(value: Operation) {
    const generation = epoch
    const documentId = doc
    if (!documentId || state.busy || !active(generation)) throw new Error('Thread context changed')
    const replayingUnknown = operation !== null
    state.busy = true
    try {
      persist(value)
      const receipt = await writeDocThread(documentId, value.id, value.action, value.body)
      if (!active(generation)) throw new Error('Thread context changed')
      install(receipt)
      persist(null)
      state.errors[value.id] = ''
    } catch (cause) {
      if (active(generation)) {
        state.errors[value.id] = cause instanceof Error ? cause.message : String(cause)
        if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408) {
          // A rejected replay cannot disprove the original committed write.
          if (!replayingUnknown) persist(null)
          if (cause.status === 409) await refresh()
        }
      }
      throw cause
    } finally {
      if (active(generation)) state.busy = false
    }
  }
  async function write(id: string, action: Operation['action'], content?: string) {
    if (operation || state.busy) throw new Error('Previous thread operation needs recovery')
    const thread = state.threads.find((t) => t.comment.id === id)
    if (!thread) {
      await refresh()
      return
    }
    await execute({
      id,
      action,
      body: {
        operation_id: crypto.randomUUID(),
        expected_revision: thread.revision,
        ...(content === undefined ? {} : { content }),
      },
    })
  }
  const actions: DocThreadActions = {
    reply: (id, content) => write(id, 'replies', content),
    resolve: (id) => write(id, 'resolve'),
    reopen: (id) => write(id, 'reopen'),
    recover: async (id) => {
      if (operation?.id === id) {
        const frozen = operation
        await execute(frozen)
        return frozen.action === 'replies' ? { reply: frozen.body.content! } : undefined
      }
      await refresh()
      return undefined
    },
    stopAgent: async (id) => {
      if (doc) await stopDocThreadAgent(doc, id)
    },
  }
  function reset() {
    epoch++
    doc = document()
    actor = myId()
    state.threads = []
    state.activity = {}
    state.errors = {}
    state.busy = false
    state.unknown = null
    operation = null
    try {
      const saved = localStorage.getItem(key())
      if (saved) {
        operation = JSON.parse(saved) as Operation
        state.unknown = operation.id
      }
    } catch {
      /* No writes are sent until persistence succeeds. */
    }
    stopListening?.()
    stopListening = doc
      ? listenToComments(doc, (signal) => {
          if (signal.kind === 'changed') void refresh()
          else state.activity[signal.thread] = { state: signal.state, ...(signal.tool ? { tool: signal.tool } : {}) }
        })
      : null
    void refresh()
  }
  watch(document, reset, { immediate: true, flush: 'sync' })
  const timer = setInterval(() => {
    if (actor !== myId()) reset()
  }, 500)
  onBeforeUnmount(() => {
    disposed = true
    epoch++
    clearInterval(timer)
    stopListening?.()
  })
  return { state, actions, refresh }
}
