// 这段对话里的文件：发进来的附件、文件区里的文件（芝士交付的、定时任务交回的、新建或
// 复制来编辑的），和芝士写的文档；以及把其中一份保存到资料库（#3828）。
import type { Ref } from 'vue'

import { ref, watch } from 'vue'

import { keepDocument, listRoomAttachments, listRoomDocuments, listRoomFiles, saveRoomOutputToLibrary } from '@/api'
import { t } from '@/i18n'

/** 「文件」那一格里的一行：一份文件（`id` 是它的路径），或一份文档（`id` 是文档 id）。 */
export interface ConversationFile {
  kind: 'file' | 'document'
  id: string
  name: string
  /** 文件区里它所在的文件夹（`runs/2026-09-14`）；附件和文档在最上层，是空串。 */
  folder: string
  /** 谁放进来的（handle）；说不出来是谁时是 null。 */
  added_by: string | null
  added_at: string
  in_library: boolean
}

export function fileKey(row: Pick<ConversationFile, 'kind' | 'id'>): string {
  return `${row.kind}:${row.id}`
}

function folderOf(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut < 0 ? '' : path.slice(0, cut)
}

export function useConversationFiles(topicId: Ref<string | null>) {
  const rows = ref<ConversationFile[]>([])
  /** 文件区的清单不是全部：超过上限的那部分没有列出来。 */
  const truncated = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const keeping = ref(new Set<string>())

  async function load() {
    const id = topicId.value
    if (!id) return
    loading.value = true
    error.value = null
    try {
      const [sent, area, docs] = await Promise.all([listRoomAttachments(id), listRoomFiles(id), listRoomDocuments(id)])
      truncated.value = area.truncated
      rows.value = [
        ...sent.data.map<ConversationFile>((f) => ({
          kind: 'file',
          id: f.path,
          name: f.name,
          folder: '',
          added_by: f.added_by,
          added_at: f.added_at,
          in_library: f.in_library,
        })),
        ...area.data.map<ConversationFile>((f) => ({
          kind: 'file',
          id: f.path,
          name: f.path.split('/').pop() ?? f.path,
          folder: folderOf(f.path),
          added_by: f.author,
          added_at: f.modified_at,
          in_library: f.in_library,
        })),
        ...docs.data.map<ConversationFile>((d) => ({
          kind: 'document',
          id: d.id,
          name: d.title || t('work.room.doc.untitled'),
          folder: '',
          added_by: d.author,
          added_at: d.created_at,
          in_library: d.in_library,
        })),
      ].sort((a, b) => b.added_at.localeCompare(a.added_at))
    } catch (e) {
      error.value = e instanceof Error && e.message ? e.message : t('work.room.files.loadFailed')
    } finally {
      loading.value = false
    }
  }

  /** 保存到资料库。成功后这一行改成「已在资料库」，失败把原因交回给调用方。 */
  async function keep(row: ConversationFile): Promise<void> {
    const id = topicId.value
    if (!id) return
    const key = fileKey(row)
    keeping.value = new Set(keeping.value).add(key)
    try {
      if (row.kind === 'document') await keepDocument(row.id)
      else await saveRoomOutputToLibrary(id, row.id)
      rows.value = rows.value.map((r) => (fileKey(r) === key ? { ...r, in_library: true } : r))
    } finally {
      const next = new Set(keeping.value)
      next.delete(key)
      keeping.value = next
    }
  }

  watch(topicId, () => void load(), { immediate: true })

  return { rows, truncated, loading, error, keeping, load, keep }
}
