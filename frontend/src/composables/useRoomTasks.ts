// 这个频道里的任务，对话栏用来画「已派出」那一行、「创建了任务」那一行和支线下面那一行
// 写的状态。先画上次那份（缓存），背后再重取；频道说它的任务变了时再读一遍。
import type { RoomTask } from '../cx_types'

import { ref, watch } from 'vue'

import { cachedTopicPanel, fetchRoomTasks } from '../lib/topicPanelCache'

export function useRoomTasks(roomId: () => string | null | undefined) {
  const tasks = ref<RoomTask[]>([])
  async function reload() {
    const id = roomId()
    if (!id) return
    try {
      const rows = (await fetchRoomTasks(id)).data
      if (roomId() === id) tasks.value = rows
    } catch {
      // 状态是派生出来的装饰，不是内容。拉不到就照旧画上一份，不该让整个时间线红掉。
    }
  }
  watch(
    roomId,
    (id) => {
      tasks.value = id ? cachedTopicPanel('roomTasks', id)?.data ?? [] : []
      void reload()
    },
    { immediate: true }
  )
  return { tasks, reload }
}
