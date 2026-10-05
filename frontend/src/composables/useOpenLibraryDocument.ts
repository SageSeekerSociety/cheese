// 资料库里正看着的那一份文档：地址上是它的编号（`?doc=`），刷新、发给别人都回到这一份。
// 对话自带的那一份属于那个对话，打开它就回对话里看。
import type { ProjectDocument } from '../api/projectDocuments'
import type { PanelDocument } from './usePanelDoc'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { closeOverlay } from '@/lib/backOut'

export function useOpenLibraryDocument(
  projectId: () => string,
  docs: { about(id: string): Promise<ProjectDocument>; renamed(id: string, title: string): void },
  failed: (message: string) => void
) {
  const route = useRoute()
  const router = useRouter()
  const selectedDocId = computed(() => (typeof route.query.doc === 'string' ? route.query.doc : ''))
  const openDocument = ref<PanelDocument | null>(null)

  function openRoom(topicId: string) {
    void router.push({ name: 'workspace-topic', params: { projectId: projectId(), topicId } })
  }

  watch(
    selectedDocId,
    async (id) => {
      openDocument.value = null
      if (!id) return
      try {
        const about = await docs.about(id)
        if (selectedDocId.value !== id) return
        if (about.topic_id) {
          void router.replace({ name: 'workspace-topic', params: { projectId: projectId(), topicId: about.topic_id } })
          return
        }
        openDocument.value = { id: about.id, projectId: about.project_id, title: about.title ?? '' }
      } catch (e) {
        if (selectedDocId.value === id) failed(e instanceof Error ? e.message : String(e))
      }
    },
    { immediate: true }
  )

  return {
    selectedDocId,
    openDocument,
    openRoom,
    openDoc: (id: string) => void router.push({ query: { doc: id } }),
    closeDoc: () => closeOverlay(router, { query: {} }),
    /** 标题在文档页上改了：开着的这一份和列表里那一行都跟着。 */
    titled(title: string) {
      if (openDocument.value) openDocument.value = { ...openDocument.value, title }
      if (selectedDocId.value) docs.renamed(selectedDocId.value, title)
    },
  }
}
