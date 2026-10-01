import type { DocThread, DocThreadActions, DocThreadState, DocThreadWrite } from '../lib/docThreadTypes'

import { onBeforeUnmount, reactive, watch } from 'vue'

import { ApiError } from '../api'
import { getDocThread, writeDocThread } from '../api/docThreads'
import { myId } from '../me'

type Operation = { id: string; action: 'replies' | 'resolve' | 'reopen'; body: DocThreadWrite }
export function useDocThreads(topic: () => string | null) {
  const state = reactive<DocThreadState>({ threads: {}, errors: {}, busy: false, unknown: null })
  let room = topic()
  let actor = myId()
  let epoch = 0
  let disposed = false
  let operation: Operation | null = null
  const key = () => `cheese.doc-thread.operation.v1:${actor}:${room}`
  const active = (generation: number) => !disposed && generation === epoch && room === topic() && actor === myId()
  function install(thread: DocThread) {
    const current = state.threads[thread.comment.id]
    if (!current || thread.revision >= current.revision) state.threads[thread.comment.id] = thread
  }
  async function load(id: string) {
    const generation = epoch
    if (!room || !active(generation)) return
    try {
      const thread = await getDocThread(room, id)
      if (active(generation)) {
        install(thread)
        state.errors[id] = ''
      }
    } catch (cause) {
      if (active(generation)) state.errors[id] = String(cause instanceof Error ? cause.message : cause)
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
    const topicId = room
    if (!topicId || state.busy || !active(generation)) throw new Error('Thread context changed')
    state.busy = true
    try {
      persist(value)
      const receipt = await writeDocThread(topicId, value.id, value.action, value.body)
      if (!active(generation)) throw new Error('Thread context changed')
      install(receipt)
      persist(null)
      await load(value.id)
    } catch (cause) {
      if (active(generation)) {
        state.errors[value.id] = cause instanceof Error ? cause.message : String(cause)
        if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408) {
          persist(null)
          if (cause.status === 409) await load(value.id)
        }
      }
      throw cause
    } finally {
      if (active(generation)) state.busy = false
    }
  }
  async function write(id: string, action: Operation['action'], content?: string) {
    if (operation || state.busy) throw new Error('Previous thread operation needs recovery')
    const thread = state.threads[id]
    if (!thread) {
      await load(id)
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
    load,
    reply: (id, content) => write(id, 'replies', content),
    resolve: (id) => write(id, 'resolve'),
    reopen: (id) => write(id, 'reopen'),
    recover: async (id) => {
      if (operation?.id === id) await execute(operation)
      else await load(id)
    },
  }
  function reset() {
    epoch++
    room = topic()
    actor = myId()
    state.threads = {}
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
  }
  watch(topic, reset, { immediate: true, flush: 'sync' })
  const timer = setInterval(() => {
    if (actor !== myId()) reset()
  }, 500)
  onBeforeUnmount(() => {
    disposed = true
    epoch++
    clearInterval(timer)
  })
  return { state, actions }
}
