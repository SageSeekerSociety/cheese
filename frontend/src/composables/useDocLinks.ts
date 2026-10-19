// 一份文档的链接这一侧（#3827）：打 # 时能引用的文档，和引用了它的地方。取数在这里，
// 面板只吃结果。
import type { DocBacklink } from '../api/docLinks'
import type { LinkableDocument } from '../lib/docMentionMenu'

import { ref, watch } from 'vue'

import { getDocumentBacklinks } from '../api/docLinks'
import { linkableDocuments } from '../lib/linkableDocuments'

export function useDocLinks(source: { projectId: () => string | null; documentId: () => string | null }) {
  const linkable = ref<LinkableDocument[]>([])
  const backlinks = ref<DocBacklink[]>([])

  let listed: string | null = null
  watch(
    source.projectId,
    async (projectId) => {
      if (!projectId || projectId === listed) return
      listed = projectId
      try {
        const docs = await linkableDocuments(projectId)
        if (listed === projectId) linkable.value = docs
      } catch {
        // 引用一份文档是写作时的一个便利，取不到就只列频道。
        if (listed === projectId) linkable.value = []
      }
    },
    { immediate: true }
  )

  let sequence = 0
  async function refreshBacklinks() {
    const did = source.documentId()
    const mine = ++sequence
    if (!did) {
      backlinks.value = []
      return
    }
    try {
      const found = await getDocumentBacklinks(did)
      if (mine === sequence) backlinks.value = found.linked_from
    } catch {
      if (mine === sequence) backlinks.value = []
    }
  }
  watch(source.documentId, refreshBacklinks, { immediate: true })

  return { linkable, backlinks, refreshBacklinks }
}
