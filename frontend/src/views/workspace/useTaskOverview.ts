// 任务概览右边那一列要的数据：从哪来（相关）、芝士这一轮的清单、第一轮整理失败后
// 的重试。任务本身由 useTaskPage 拿着。
import type { TodoItem } from '@/cx_types'
import type { TaskRelated } from '@/types/taskOrigin'

import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { getTaskRelated, retryTaskOpening } from '@/api/tasks'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { roomProgressQuery } from '@/query/room'
import { fromSnapshot } from '@/query/snapshot'

/** 芝士在干活时，清单多久重取一次。 */
const CHECKLIST_EVERY_MS = 5000

export function useTaskOverview(opts: {
  taskId: () => string | undefined
  /** 这件任务里有一轮在跑。 */
  working: () => boolean
  /** 重读任务本身（第一轮的状态在它上面）。 */
  reloadTask: () => Promise<void>
}) {
  const related = ref<TaskRelated | null>(null)
  async function loadRelated() {
    const id = opts.taskId()
    if (!id) return
    try {
      // 只有进任务时的第一次读用房间快照里那一块（query/snapshot），之后每次都问服务器。
      const got = await fromSnapshot(keys.taskRelated(id), () => getTaskRelated(id))
      if (opts.taskId() === id) related.value = got
    } catch {
      // 相关是背景信息，拿不到就不画。
    }
  }

  // 清单只在这一轮跑着的时候有：它说的是「此刻在做哪一步」。跑着时每 5 秒问一次。
  const progress = useQuery(
    computed(() => {
      const id = opts.taskId() ?? ''
      return {
        ...roomProgressQuery(id),
        enabled: !!id && opts.working(),
        refetchInterval: CHECKLIST_EVERY_MS,
        staleTime: 0,
      }
    }),
    queryClient
  )
  // 上一轮留下的那份清单不算：说的是那时候在做的事。只认这一轮开始之后问到的。
  const since = ref(0)
  watch(
    () => opts.working(),
    (working) => {
      if (working) since.value = Date.now()
    },
    { immediate: true }
  )
  const checklist = computed<TodoItem[]>(() =>
    opts.working() && progress.dataUpdatedAt.value >= since.value ? progress.data.value?.items ?? [] : []
  )

  watch(
    () => opts.taskId(),
    () => {
      related.value = null
      void loadRelated()
    },
    { immediate: true }
  )

  const retrying = ref(false)
  const retryError = ref<string | null>(null)
  async function retryOpening() {
    const id = opts.taskId()
    if (!id || retrying.value) return
    retrying.value = true
    retryError.value = null
    try {
      await retryTaskOpening(id)
      await opts.reloadTask()
    } catch (e) {
      retryError.value = e instanceof Error ? e.message : String(e)
    } finally {
      retrying.value = false
    }
  }

  return { related, checklist, retrying, retryError, retryOpening, loadRelated }
}
