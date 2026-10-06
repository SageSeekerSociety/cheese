// 一个频道的支线清单：概览里「支线」那一格读它。频道说它的支线变了（`threads`）就重读。
import type { ThreadRow } from '../types/threads'

import { computed, ref } from 'vue'

import { listThreads } from '@/api/threads'
import { t } from '@/i18n'

export function useChannelThreads(roomId: () => string | null) {
  const rows = ref<ThreadRow[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    const room = roomId()
    if (!room) {
      rows.value = []
      return
    }
    loading.value = true
    try {
      const got = await listThreads(room)
      if (roomId() !== room) return
      rows.value = Array.isArray(got) ? got : []
      error.value = null
    } catch {
      if (roomId() === room) error.value = t('work.room.thread.loadFailed')
    } finally {
      if (roomId() === room) loading.value = false
    }
  }

  /** 有我没读过的回复：页签上挂一个点。 */
  const hasNew = computed(() => rows.value.some((row) => row.unread))

  /** 我刚打开了这一条：不等下一次重读，先把点去掉。 */
  function markSeen(threadId: string) {
    rows.value = rows.value.map((row) => (row.id === threadId ? { ...row, unread: false } : row))
  }

  return { rows, loading, error, hasNew, load, markSeen }
}
