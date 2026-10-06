// 任务产出那一块的取数：列出 AI 队友摆出来的东西、存进资料库、从模板新建一份。
// 组件只画，读写都在这里（组件不直接碰接口层）。
import type { DocumentTemplate, RoomOutput } from '@/types/roomOutput'

import { ref, watch } from 'vue'

import { listDocumentTemplates, listRoomOutputs, newFromTemplate, saveRoomOutputToLibrary } from '@/api'

export type { DocumentTemplate, RoomOutput }

export function useTaskOutputs(taskId: () => string | null) {
  const outputs = ref<RoomOutput[]>([])
  const templates = ref<DocumentTemplate[]>([])

  async function load() {
    const id = taskId()
    if (!id) return
    try {
      outputs.value = (await listRoomOutputs(id)).data
    } catch {
      // 读不到这一块就不显示列表：这一列的主体是上面的文档。
      outputs.value = []
    }
  }
  watch(taskId, () => void load(), { immediate: true })

  /** 存进资料库，答出它在那边叫什么（撞名时会加 `(2)`）。 */
  async function save(path: string): Promise<string | null> {
    const id = taskId()
    if (!id) return null
    return (await saveRoomOutputToLibrary(id, path)).name
  }

  async function loadTemplates() {
    const id = taskId()
    if (!id || templates.value.length) return
    try {
      templates.value = (await listDocumentTemplates(id)).data
    } catch {
      templates.value = []
    }
  }

  /** 照模板建一份，答出建在哪。 */
  async function create(templateId: string, path: string): Promise<string | null> {
    const id = taskId()
    if (!id) return null
    const made = await newFromTemplate(id, templateId, path)
    await load()
    return made.path
  }

  return { outputs, templates, load, save, loadTemplates, create }
}
