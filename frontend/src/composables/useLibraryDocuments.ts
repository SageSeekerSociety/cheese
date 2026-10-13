// 资料库里的文档：按标题和正文找、新建、另存一份、删掉。
//
// 列表不在这里：文档和文件一起一页一页地列（`useLibraryPages`）。找的时候问后端：
// 文档要按正文找，对话自带的那几份也要找得到，这两样前端都答不了。
import type { DocumentSearch, ProjectDocument } from '../api/projectDocuments'

import { onBeforeUnmount, ref, watch } from 'vue'

import {
  createProjectDocument,
  deleteDocument,
  getDocumentAbout,
  searchProjectDocuments,
} from '../api/projectDocuments'

/** 停手这么久才去搜：一个字一个字地打，不必每个字问一次。 */
const SEARCH_PAUSE_MS = 250

export function useLibraryDocuments(projectId: () => string, query: () => string) {
  const found = ref<DocumentSearch | null>(null)
  const searching = ref(false)

  let searchSequence = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  watch(
    () => [projectId(), query().trim()] as const,
    ([pid, q]) => {
      if (timer) clearTimeout(timer)
      const sequence = ++searchSequence
      if (!q) {
        found.value = null
        searching.value = false
        return
      }
      searching.value = true
      timer = setTimeout(async () => {
        try {
          const result = await searchProjectDocuments(pid, q)
          if (sequence === searchSequence) found.value = result
        } catch {
          if (sequence === searchSequence) found.value = { query: q, library: [], rooms: [] }
        } finally {
          if (sequence === searchSequence) searching.value = false
        }
      }, SEARCH_PAUSE_MS)
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    if (timer) clearTimeout(timer)
  })

  /** 正在建的那一份：`''` 是新建空的，否则是正被另存的那一份的编号。 */
  const making = ref<string | null>(null)
  /** 新建一份空的；`copyOf` 给了就另存那一份现在的样子。回执是新的那一份的编号。 */
  async function make(copyOf?: string): Promise<string> {
    making.value = copyOf ?? ''
    try {
      const made = await createProjectDocument(projectId(), copyOf ? { copy_of: copyOf } : {})
      return made.id
    } finally {
      making.value = null
    }
  }

  async function remove(id: string) {
    await deleteDocument(id)
  }

  /** 地址上点名的那一份。 */
  function about(id: string): Promise<ProjectDocument> {
    return getDocumentAbout(id)
  }

  return { found, searching, making, make, remove, about }
}
