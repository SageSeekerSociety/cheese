// 「这份房间文件保存过的每一版」：读清单、下载某一版、把某一版恢复成最新版。
//
// 和画的那一半（`components/panels/preview/RoomFileHistory.vue`）分家的理由和别处一样：
// 那一只原先自己 import 三个接口函数，于是「预览」和它底下那个编辑器都跟着它够得着
// 接口层——两处都在场景里，场景只吃 props 和事件。
//
// 哪一份、什么时候重读、恢复完通知谁，全在这里；画的那一半只认 props。
import type { RoomFileRevision } from '../api'

import { ref, watch } from 'vue'

import { downloadRoomFileRevision, restoreRoomFileRevision, roomFileRevisions } from '../api'

import { t } from '@/i18n'

export interface RoomFileHistoryScope {
  topicId: () => string | null
  /** 看的是哪一份；null 就是没有可看的。编辑器开着时是编辑器里那份，否则是预览台上那份。 */
  path: () => string | null
  /** 这个文件现在是哪一版：它变了就重读一遍（编辑器里刚存下的那一版）。 */
  version?: () => string | null
  /** 这一份历史此刻要不要读。关着的那些先不读，读回来的也不画。 */
  enabled?: () => boolean
}

export interface RoomFileHistoryHooks {
  /** 恢复了一版：文件变了，宿主该把那一页重画一遍。 */
  onRestored?: (revision: RoomFileRevision) => void
}

export function useRoomFileHistory(scope: RoomFileHistoryScope, hooks: RoomFileHistoryHooks = {}) {
  const rows = ref<RoomFileRevision[]>([])
  const loading = ref(false)
  const error = ref('')
  const busy = ref<string | null>(null)

  async function load() {
    const tid = scope.topicId()
    const path = scope.path()
    if (!tid || !path || scope.enabled?.() === false) {
      rows.value = []
      return
    }
    loading.value = true
    error.value = ''
    try {
      rows.value = (await roomFileRevisions(tid, path)).data
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.room.fileHistory.loadFailed')
    } finally {
      loading.value = false
    }
  }

  /** 把某一版恢复成最新版。做成了没有要回话：画的那一半拿它决定收不收那个确认框。 */
  async function restore(row: RoomFileRevision): Promise<boolean> {
    const tid = scope.topicId()
    if (!tid) return false
    busy.value = row.id
    error.value = ''
    try {
      const made = await restoreRoomFileRevision(tid, row.id)
      hooks.onRestored?.(made)
      await load()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.room.fileHistory.restoreFailed')
      return false
    } finally {
      busy.value = null
    }
  }

  async function download(row: RoomFileRevision) {
    const tid = scope.topicId()
    if (!tid) return
    try {
      await downloadRoomFileRevision(tid, row)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.room.fileHistory.downloadFailed')
    }
  }

  watch(
    [() => scope.topicId(), () => scope.path(), () => scope.version?.() ?? null, () => scope.enabled?.() ?? true],
    () => void load(),
    { immediate: true }
  )

  return { rows, loading, error, busy, load, restore, download }
}

/** 「房间文件历史」这一份取数原样递给面板（props）：面板自己不认识接口。 */
export type RoomFileHistoryBundle = ReturnType<typeof useRoomFileHistory>
