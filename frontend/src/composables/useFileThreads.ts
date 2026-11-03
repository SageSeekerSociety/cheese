// 一份房间文件上的评论串：整串读回来，一次写一个操作。
//
// 和文档那套是同一件事（`useDocThreads`）：线程、回复、解决、重开，一次写按操作 id
// 幂等。两处不同的是取数的地址，和「谁在这儿听」——文档有一根协同连接，写完由服务
// 推给开着的人；文件没有，所以写完自己重读一遍，别人写的要等下次打开。
//
// 写没写成功不知道时（连接断了、页面刷到一半），那份操作记在 localStorage 里，回来
// 时拿同一个操作 id 再发一次；服务端一个操作 id 只落一次。
import type { DocThread, DocThreadActions, DocThreadState, DocThreadWrite } from '../lib/docThreadTypes'

import { onBeforeUnmount, reactive, watch } from 'vue'

import { ApiError } from '../api'
import { listFileThreads, stopFileThreadAgent, writeFileThread } from '../api/fileThreads'
import { storageGetJson, storageRemove, storageSetJson } from '../lib/browserStorage'
import { myId } from '../me'

type Operation = { id: string; action: 'replies' | 'resolve' | 'reopen'; body: DocThreadWrite }

/** 这一包递给画的那一层（props）：取数在这一层，面板不再自己去取。 */
export type FileThreadsBundle = ReturnType<typeof useFileThreads>

export function useFileThreads(
  topic: () => string | null,
  path: () => string | null,
  /** 这一栏开着没有：没开就不去问，打开时再读。 */
  wanted: () => boolean = () => true
) {
  const state = reactive<DocThreadState>({ threads: [], activity: {}, errors: {}, busy: false, unknown: null })
  let where = [topic(), path()] as const
  let actor = myId()
  let epoch = 0
  let reads = 0
  let disposed = false
  let operation: Operation | null = null
  const key = () => `cheese.file-thread.operation.v1:${actor}:${topic()}:${path()}`
  const current = () => topic() === where[0] && path() === where[1]
  const active = (generation: number) => !disposed && generation === epoch && current() && actor === myId()

  function install(thread: DocThread) {
    const at = state.threads.findIndex((t) => t.comment.id === thread.comment.id)
    if (at < 0) state.threads.push(thread)
    else if (thread.revision >= state.threads[at].revision) state.threads[at] = { ...state.threads[at], ...thread }
  }

  /** 这份文件上的所有线程重读一遍。 */
  async function refresh() {
    const generation = epoch
    const sequence = ++reads
    const tid = topic()
    const file = path()
    if (!tid || !file || !wanted() || !active(generation)) return
    try {
      const { data } = await listFileThreads(tid, file)
      if (!active(generation) || sequence !== reads) return
      state.threads = data
      // 有谁在答，就接着看：文件上的评论没有协同连接可听，进度只写在列表里
      // （`answering`）。答完、停下、或者谁也答不上来的时候它变空，循环就停。
      if (data.some((thread) => thread.answering)) armWatch()
      else disarmWatch()
    } catch {
      // 屏上这一份留着；下次刷新再读。
    }
  }

  /** 还有人在答时每隔这么久重读一次列表。 */
  const WATCH_MS = 2000
  let watchTimer: ReturnType<typeof setInterval> | null = null
  function armWatch() {
    if (watchTimer !== null) return
    watchTimer = setInterval(() => void refresh(), WATCH_MS)
  }
  function disarmWatch() {
    if (watchTimer === null) return
    clearInterval(watchTimer)
    watchTimer = null
  }

  function persist(value: Operation | null) {
    if (value) storageSetJson(key(), value)
    else storageRemove(key())
    operation = value
    state.unknown = value?.id ?? null
  }

  async function execute(value: Operation) {
    const generation = epoch
    const tid = topic()
    const file = path()
    if (!tid || !file || state.busy || !active(generation)) throw new Error('Thread context changed')
    const replayingUnknown = operation !== null
    state.busy = true
    try {
      persist(value)
      const receipt = await writeFileThread(tid, file, value.id, value.action, value.body)
      if (!active(generation)) throw new Error('Thread context changed')
      install(receipt)
      persist(null)
      state.errors[value.id] = ''
      return receipt
    } catch (cause) {
      if (active(generation)) {
        state.errors[value.id] = cause instanceof Error ? cause.message : String(cause)
        if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408) {
          // 被拒的一次重发并不能证明原来那次没落库。
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
    return execute({
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
    resolve: async (id) => {
      await write(id, 'resolve')
    },
    reopen: async (id) => {
      await write(id, 'reopen')
    },
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
      const tid = topic()
      const file = path()
      if (tid && file) await stopFileThreadAgent(tid, file, id)
      await refresh()
    },
  }

  function reset() {
    epoch++
    disarmWatch()
    where = [topic(), path()]
    actor = myId()
    state.threads = []
    state.activity = {}
    state.errors = {}
    state.busy = false
    state.unknown = null
    operation = null
    const saved = storageGetJson<Partial<Operation>>(key())
    if (saved?.id && saved.action && saved.body) {
      operation = saved as Operation
      state.unknown = operation.id
    }
    void refresh()
  }

  watch([topic, path, wanted], reset, { immediate: true, flush: 'sync' })
  const timer = setInterval(() => {
    if (actor !== myId()) reset()
  }, 500)
  onBeforeUnmount(() => {
    disposed = true
    epoch++
    clearInterval(timer)
    disarmWatch()
  })
  return { state, actions, refresh }
}
