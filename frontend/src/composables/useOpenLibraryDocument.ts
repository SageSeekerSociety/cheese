// 资料库里正看着的那一份文档：它有自己的地址（`/projects/<短名>/docs/<编号>`），整页打开，
// 刷新、发给别人都回到这一份。
import type { ProjectDocument } from '../api/projectDocuments'
import type { PanelDocument } from './usePanelDoc'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { closeOverlay } from '@/lib/backOut'

export function useOpenLibraryDocument(
  projectId: () => string,
  docId: () => string | undefined,
  docs: { about(id: string): Promise<ProjectDocument>; renamed(id: string, title: string): void },
  failed: (message: string) => void
) {
  const router = useRouter()
  const selectedDocId = computed(() => docId() ?? '')
  const openDocument = ref<PanelDocument | null>(null)

  /** 回频道，给了任务就进那个任务。 */
  function openRoom(topicId: string, taskId?: string | null) {
    if (taskId) void router.push({ name: 'workspace-task', params: { projectId: projectId(), topicId, taskId } })
    else void router.push({ name: 'workspace-topic', params: { projectId: projectId(), topicId } })
  }

  watch(
    selectedDocId,
    async (id) => {
      openDocument.value = null
      if (!id) return
      try {
        const about = await docs.about(id)
        if (selectedDocId.value !== id) return
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
    openDoc: (id: string) =>
      void router.push({ name: 'project-document', params: { projectId: projectId(), docId: id } }),
    closeDoc: () => closeOverlay(router, { name: 'project-library', params: { projectId: projectId() } }),
    /** 标题在文档页上改了：开着的这一份和列表里那一行都跟着。 */
    titled(title: string) {
      if (openDocument.value) openDocument.value = { ...openDocument.value, title }
      if (selectedDocId.value) docs.renamed(selectedDocId.value, title)
    },
  }
}
