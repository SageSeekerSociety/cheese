// 一个频道的支线清单：概览里「支线」那一格读它。频道说它的支线变了（`threads`）就重读。
// 和主线消息下面那一行读同一份（`query/room`）。
import type { ThreadRow } from '../types/threads'

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { t } from '@/i18n'
import { patchQuery, queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { threadsQuery } from '@/query/room'

export function useChannelThreads(roomId: () => string | null) {
  const read = useQuery(
    computed(() => {
      const room = roomId() ?? ''
      return { ...threadsQuery(room), enabled: !!room }
    }),
    queryClient
  )
  const rows = computed<ThreadRow[]>(() => read.data.value ?? [])
  const loading = computed(() => read.isFetching.value)
  const error = computed<string | null>(() => (read.isError.value ? t('work.room.thread.loadFailed') : null))

  function load() {
    const room = roomId()
    if (room) void queryClient.invalidateQueries({ queryKey: keys.roomThreads(room) })
  }

  /** 有我没读过的回复：页签上挂一个点。 */
  const hasNew = computed(() => rows.value.some((row) => row.unread))

  /** 我刚打开了这一条：不等下一次重读，先把点去掉。 */
  function markSeen(threadId: string) {
    const room = roomId()
    if (!room) return
    void patchQuery<ThreadRow[]>(keys.roomThreads(room), (held) =>
      held.map((row) => (row.id === threadId ? { ...row, unread: false } : row))
    )
  }

  return { rows, loading, error, hasNew, load, markSeen }
}
