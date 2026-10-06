// 任务概览右边那一列要的数据：从哪来（相关）、芝士这一轮的清单、第一轮整理失败后
// 的重试。任务本身由 useTaskPage 拿着。
import type { TodoItem } from '@/cx_types'
import type { TaskRelated } from '@/types/taskOrigin'

import { onBeforeUnmount, ref, watch } from 'vue'

import { getTaskRelated, retryTaskOpening } from '@/api/tasks'
import { fetchTopicProgress } from '@/lib/topicPanelCache'

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
      const got = await getTaskRelated(id)
      if (opts.taskId() === id) related.value = got
    } catch {
      // 相关是背景信息，拿不到就不画。
    }
  }

  // 清单只在这一轮跑着的时候有：它说的是「此刻在做哪一步」。
  const checklist = ref<TodoItem[]>([])
  let timer: ReturnType<typeof setInterval> | undefined
  async function loadChecklist() {
    const id = opts.taskId()
    if (!id) return
    try {
      const progress = await fetchTopicProgress(id, { fresh: true })
      if (opts.taskId() === id && opts.working()) checklist.value = progress.items ?? []
    } catch {
      // 同上。
    }
  }
  watch(
    () => [opts.taskId(), opts.working()] as const,
    ([id, working]) => {
      clearInterval(timer)
      timer = undefined
      if (!id || !working) {
        checklist.value = []
        return
      }
      void loadChecklist()
      timer = setInterval(() => void loadChecklist(), CHECKLIST_EVERY_MS)
    },
    { immediate: true }
  )
  onBeforeUnmount(() => clearInterval(timer))

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
