// 这段对话里的文件：发进来的附件、芝士写的文档，和文件区里的东西；以及把其中一份保存到
// 资料库（#3828）。一行一样东西，新的在前，不按文件夹分。
//
// 文件区里不是每一份都是一样东西：摆出来的一页网页连着它自己的样式、脚本、图片一起存下来，
// 芝士干活时也会留下中间的文件。所以文件区里列的是摆出来过的（`listRoomOutputs`，频道连同它
// 的任务和支线），加上人自己放进来、新建的；一页网页旁边那些它自己的文件不单列。
import type { Ref } from 'vue'
import type { RoomFile } from '@/api'
import type { RoomOutput } from '@/types/roomOutput'

import { ref, watch } from 'vue'

import {
  keepDocument,
  listRoomAttachments,
  listRoomDocuments,
  listRoomFiles,
  listRoomOutputs,
  saveRoomOutputToLibrary,
} from '@/api'
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

/** 一页网页（摆出来的）所在的文件夹；在最上层的不算：那一层不是它一个人的。 */
function pageFolder(output: RoomOutput): string | null {
  if (!/\.html?$/i.test(output.path)) return null
  return folderOf(output.path) || null
}

/**
 * 文件区里哪几份算一样东西：摆出来过的，和人自己放进来、新建的；摆出来的网页文件夹里它自己的
 * 那些文件不算。`shown_at` 是它摆出来的时候，没摆过的是它最后改的时候。
 */
export function areaThings(area: RoomFile[], shown: RoomOutput[]): (RoomFile & { at: string })[] {
  const shownAt = new Map<string, string>()
  for (const output of shown)
    if (output.kind === 'file' && !shownAt.has(output.path)) shownAt.set(output.path, output.shown_at)
  const pages = shown.map(pageFolder).filter((folder): folder is string => !!folder)
  const inPage = (path: string) => pages.some((folder) => path.startsWith(`${folder}/`))
  return area.flatMap((file) => {
    const at = shownAt.get(file.path)
    if (at) return [{ ...file, at }]
    if (file.author_kind !== 'human' || inPage(file.path)) return []
    return [{ ...file, at: file.modified_at }]
  })
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
      const [sent, area, shown, docs] = await Promise.all([
        listRoomAttachments(id),
        listRoomFiles(id),
        listRoomOutputs(id),
        listRoomDocuments(id),
      ])
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
        ...areaThings(area.data, shown.data).map<ConversationFile>((f) => ({
          kind: 'file',
          id: f.path,
          name: f.path.split('/').pop() ?? f.path,
          folder: folderOf(f.path),
          added_by: f.author,
          added_at: f.at,
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

export type ConversationFiles = ReturnType<typeof useConversationFiles>
