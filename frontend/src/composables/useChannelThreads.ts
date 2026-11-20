// 一个频道的支线清单：概览里「支线」那一格读它。频道说它的支线变了（`threads`）就重读。
// 和主线消息下面那一行读同一份（`query/room`）。
import type { ThreadLists, ThreadRow } from '../types/threads'

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
  const lists = computed<ThreadLists>(() => read.data.value ?? { open: [], recycled: [] })
  const rows = computed<ThreadRow[]>(() => lists.value.open)
  /** 收起来的那几栏（`thread/recycle.py`）：报错满三天、没人回来的支线，点开还能读。 */
  const recycled = computed<ThreadRow[]>(() => lists.value.recycled)
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
    void patchQuery<ThreadLists>(keys.roomThreads(room), (held) => ({
      ...held,
      open: held.open.map((row) => (row.id === threadId ? { ...row, unread: false } : row)),
    }))
  }

  /** 刚把一条放了回来：不等重读，先把它从「已回收」挪回默认那一栏。 */
  function markRestored(threadId: string) {
    const room = roomId()
    if (!room) return
    void patchQuery<ThreadLists>(keys.roomThreads(room), (held) => {
      const row = held.recycled.find((item) => item.id === threadId)
      if (!row) return held
      return {
        open: [{ ...row, recycled_at: null }, ...held.open],
        recycled: held.recycled.filter((item) => item.id !== threadId),
      }
    })
  }

  return { rows, recycled, loading, error, hasNew, load, markSeen, markRestored }
}
