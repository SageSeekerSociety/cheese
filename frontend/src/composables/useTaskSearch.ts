// The 编辑器 form's 搜索: the text of a task's files searched as the person types.
//
// A search goes out once typing settles (`SEARCH_AFTER_MS`), or at once on Enter;
// a newer one cancels the one in flight, and an answer that arrives after a newer
// search started is dropped, so what is listed is always the answer to what is in
// the box. The last answer stays on screen while the next one is on its way.
//
// Searching is doing, as editing is: only a copy in the task's environment is
// searched, and a search of a copy no environment holds brings one up (the
// backend starts it and answers that it is being prepared). The search then
// waits on the page's one state of the environment (`useTaskEnvironment`) and
// goes out again once it is ready, without the person typing it again. A reader
// who may not start the environment is told the search needs it, with no retry.
import type { FileSearchResult } from '../api'

import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { ApiError, searchTaskFiles } from '../api'

import { useTaskEnvironment } from './useTaskEnvironment'

export type { FileSearchLine, FileSearchResult } from '../api'

/** How long typing settles before a search goes out (ms). */
export const SEARCH_AFTER_MS = 300

/**
 * `idle`: nothing typed. `searching`: a search is on its way. `environment`: it waits for the
 * task's environment (being prepared, or failed to be: `environment` says).
 */
export type TaskSearchState = 'idle' | 'searching' | 'environment' | 'done' | 'error'

export function useTaskSearch(opts: { projectId: () => string | null; taskId: () => string | null }) {
  const query = ref('')
  const state = ref<TaskSearchState>('idle')
  const result = shallowRef<FileSearchResult | null>(null)
  /** What the server said when the search failed. */
  const error = ref('')
  /** Searching again would answer the same (the search needs an environment this reader cannot start). */
  const final = ref(false)
  const environment = useTaskEnvironment(opts.taskId)

  let timer: ReturnType<typeof setTimeout> | undefined
  let inFlight: AbortController | null = null
  let asked = 0

  function cancel() {
    clearTimeout(timer)
    timer = undefined
    inFlight?.abort()
    inFlight = null
    asked += 1
  }

  async function run() {
    cancel()
    const text = query.value
    const pid = opts.projectId()
    const tid = opts.taskId()
    if (!text.trim() || !pid || !tid) {
      state.value = 'idle'
      result.value = null
      return
    }
    const mine = asked
    const controller = new AbortController()
    inFlight = controller
    state.value = 'searching'
    try {
      const answer = await searchTaskFiles(pid, tid, text, controller.signal)
      if (mine !== asked) return
      result.value = answer
      state.value = 'done'
    } catch (e) {
      if (mine !== asked) return
      if (environment.learn(e)) {
        state.value = 'environment'
        return
      }
      error.value = e instanceof Error ? e.message : ''
      final.value = e instanceof ApiError && e.code === 'SearchNeedsEnvironment'
      state.value = 'error'
    } finally {
      if (inFlight === controller) inFlight = null
    }
  }

  /** The text in the box changed: search once typing settles. */
  function setQuery(text: string) {
    query.value = text
    cancel()
    if (!text.trim()) {
      state.value = 'idle'
      result.value = null
      return
    }
    timer = setTimeout(() => void run(), SEARCH_AFTER_MS)
  }

  /** Search now (Enter, 重试). An environment that failed or did not answer is asked for again. */
  function searchNow() {
    const env = environment.state.value
    if (state.value === 'environment' && (env === 'failed' || env === 'unanswered')) void environment.retry()
    else void run()
  }

  // The environment the search waits for is ready: it goes out again.
  watch(
    () => environment.state.value,
    (now) => {
      if (now === 'ready' && state.value === 'environment') void run()
    }
  )

  // Another task's files: what was found was in the last one's.
  watch(opts.taskId, () => setQuery(''))
  onBeforeUnmount(cancel)

  return { query, state, result, error, final, environment, setQuery, searchNow }
}

export type TaskSearchBundle = ReturnType<typeof useTaskSearch>
