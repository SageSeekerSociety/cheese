// The 编辑器 form's 搜索: the text of a task's files searched as the person types.
//
// A search goes out once typing settles (`SEARCH_AFTER_MS`), or at once on Enter;
// a newer one cancels the one in flight, and an answer that arrives after a newer
// search started is dropped, so what is listed is always the answer to what is in
// the box. The last answer stays on screen while the next one is on its way.
import type { FileSearchResult } from '../api'

import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { searchTaskFiles } from '../api'

export type { FileSearchLine, FileSearchResult } from '../api'

/** How long typing settles before a search goes out (ms). */
export const SEARCH_AFTER_MS = 300

/** `idle`: nothing typed. `searching`: a search is on its way. */
export type TaskSearchState = 'idle' | 'searching' | 'done' | 'error'

export function useTaskSearch(opts: { projectId: () => string | null; taskId: () => string | null }) {
  const query = ref('')
  const state = ref<TaskSearchState>('idle')
  const result = shallowRef<FileSearchResult | null>(null)
  /** What the server said when the search failed. */
  const error = ref('')

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
      error.value = e instanceof Error ? e.message : ''
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

  /** Search now (Enter, 重试). */
  function searchNow() {
    void run()
  }

  // Another task's files: what was found was in the last one's.
  watch(opts.taskId, () => setQuery(''))
  onBeforeUnmount(cancel)

  return { query, state, result, error, setQuery, searchNow }
}

export type TaskSearchBundle = ReturnType<typeof useTaskSearch>
