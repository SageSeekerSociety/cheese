// 任务概览右边那一列要的数据：任务的卡片、从哪来（相关）、芝士这一轮的清单、第一轮
// 整理失败后的重试。任务本身由 useTaskPage 拿着。
import type { TodoItem } from '@/cx_types'
import type { MapCardChange, MapCardView } from '@/types/taskMapCard'
import type { TaskRelated } from '@/types/taskOrigin'

import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { changeTaskMapCard, getTaskMapCard, getTaskRelated, retryTaskOpening } from '@/api/tasks'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { roomProgressQuery } from '@/query/room'
import { fromSnapshot, lastSeen, seen } from '@/query/snapshot'

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
      seen(keys.taskRelated(id), got)
      if (opts.taskId() === id) related.value = got
    } catch {
      // 相关是背景信息，拿不到就不画。
    }
  }

  // 任务的卡片。芝士或别人改了它，任务的连接推来 `task_map_card`，页面再读一次。
  const mapCard = ref<MapCardView | null>(null)
  const mapCardSaving = ref(false)
  const mapCardError = ref<string | null>(null)
  async function loadMapCard() {
    const id = opts.taskId()
    if (!id) return
    try {
      const got = await getTaskMapCard(id)
      if (opts.taskId() === id) mapCard.value = got
    } catch {
      // 卡片拿不到就不画，下面的概览照常。
    }
  }
  async function saveMapCard(change: MapCardChange) {
    const id = opts.taskId()
    if (!id || mapCardSaving.value) return
    mapCardSaving.value = true
    mapCardError.value = null
    try {
      const got = await changeTaskMapCard(id, change)
      if (opts.taskId() === id) mapCard.value = got
    } catch (e) {
      mapCardError.value = e instanceof Error && e.message ? e.message : t('work.task.mapCard.saveFailed')
    } finally {
      mapCardSaving.value = false
    }
  }

  // 清单只在这一轮跑着的时候有：它说的是「此刻在做哪一步」。跑起来时问一次，之后芝士
  // 每改一次，任务的房间连接推来新的一份（`todo` 帧，`checklistPushed`）。
  const progress = useQuery(
    computed(() => {
      const id = opts.taskId() ?? ''
      return {
        ...roomProgressQuery(id),
        enabled: !!id && opts.working(),
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
    (id) => {
      // 先画上一次在这件任务里看到的，读回来再换（见 query/snapshot 的 `lastSeen`）。
      related.value = (id && lastSeen<TaskRelated>(keys.taskRelated(id))) || null
      void loadRelated()
      mapCard.value = null
      mapCardError.value = null
      void loadMapCard()
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

  return {
    related,
    checklist,
    retrying,
    retryError,
    retryOpening,
    loadRelated,
    mapCard,
    mapCardSaving,
    mapCardError,
    loadMapCard,
    saveMapCard,
  }
}
