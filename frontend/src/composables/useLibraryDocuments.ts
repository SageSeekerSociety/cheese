// 资料库里的文档：列出来、按标题和正文找、新建、另存一份、删掉。
//
// 文件那一半（上传、替换、下载）还在资料库页里；这里只管文档，页面把两样摆进同一张
// 列表。找的时候问后端：文档要按正文找，对话自带的那几份也要找得到，这两样前端都
// 答不了。
import type { DocumentSearch, ProjectDocument } from '../api/projectDocuments'

import { onBeforeUnmount, ref, watch } from 'vue'

import {
  createProjectDocument,
  deleteDocument,
  getDocumentAbout,
  listProjectDocuments,
  searchProjectDocuments,
} from '../api/projectDocuments'

/** 停手这么久才去搜：一个字一个字地打，不必每个字问一次。 */
const SEARCH_PAUSE_MS = 250

export function useLibraryDocuments(projectId: () => string, query: () => string) {
  const documents = ref<ProjectDocument[]>([])
  const loadError = ref('')
  const found = ref<DocumentSearch | null>(null)
  const searching = ref(false)

  let listSequence = 0
  async function load() {
    const pid = projectId()
    const sequence = ++listSequence
    try {
      const listed = await listProjectDocuments(pid)
      if (sequence !== listSequence) return
      documents.value = listed.data
      loadError.value = ''
    } catch (cause) {
      if (sequence === listSequence) loadError.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

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

  watch(
    projectId,
    () => {
      documents.value = []
      void load()
    },
    { immediate: true }
  )

  /** 正在建的那一份：`''` 是新建空的，否则是正被另存的那一份的编号。 */
  const making = ref<string | null>(null)
  /** 新建一份空的；`copyOf` 给了就另存那一份现在的样子。回执是新的那一份的编号。 */
  async function make(copyOf?: string): Promise<string> {
    making.value = copyOf ?? ''
    try {
      const made = await createProjectDocument(projectId(), copyOf ? { copy_of: copyOf } : {})
      documents.value = [made, ...documents.value]
      return made.id
    } finally {
      making.value = null
    }
  }

  async function remove(id: string) {
    await deleteDocument(id)
    documents.value = documents.value.filter((doc) => doc.id !== id)
  }

  /** 地址上点名的那一份：列表里有就用它，没有（刚建、别人刚建）就单独问。 */
  async function about(id: string): Promise<ProjectDocument> {
    return documents.value.find((doc) => doc.id === id) ?? (await getDocumentAbout(id))
  }

  /** 这一份改了名：列表里跟着改。 */
  function renamed(id: string, title: string) {
    documents.value = documents.value.map((doc) => (doc.id === id ? { ...doc, title } : doc))
  }

  return { documents, loadError, found, searching, making, load, make, remove, about, renamed }
}
