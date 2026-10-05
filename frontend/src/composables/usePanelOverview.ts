// 房间「总览」那一格的数：看板的活、上一轮的进度清单、「这个房间里的东西」。
//
// 为什么取数在这儿而不是在渲染它的那只外壳里：外壳是组件，住 `components/` 下，而
// `components/**` 不许直接够得着接口层（`pnpm run lint:boundary`）。组合式函数不受这条
// 规则管，所以取数落在这里——`components/routine/RoutinePanelHost.vue` 配
// `composables/useRoutineList.ts` 是同一个形状。
//
// 各块的重取时机是照搬原来分散在三个组件里的写法，一处理由没改：
// - 看板：切到这一格、或者一轮结束时重取（`active` / `refreshTick`）。
// - 进度：进房间取一次，之后每轮结束重取一次。
// - 产物：进房间取一次、换房间重取、每轮结束重取——一轮结束时房间里可能刚摆出一样东西。
import type { Block, DocumentTemplate, RoomOutput, RoomTask, TodoItem, Topic } from '../cx_types'

import { ref, watch } from 'vue'

import { listDocumentTemplates, listRoomOutputs, newFromTemplate, saveRoomOutputToLibrary } from '../api'
import { cachedTopicPanel, fetchRoomTasks, fetchTopicProgress } from '../lib/topicPanelCache'

import { t } from '@/i18n'

export interface PanelOverviewScope {
  topic: Topic | null
  /** 总览这一格在屏幕上。看不到的时候不去拉看板。 */
  active?: boolean
  /** 每有一轮动静就加一。 */
  refreshTick?: number
}

export type TaskRow = RoomTask & { blocks?: Block[] }

export function usePanelOverview(props: PanelOverviewScope) {
  // ---- 看板 ----
  const boardRows = ref<TaskRow[]>([])
  const boardLoading = ref(false)
  const boardError = ref<string | null>(null)
  // 手上这份是哪个房间的：切到另一个房间时，缓存里那份才该顶上来。
  let boardFor: string | null = null

  async function loadBoard(opts: { fresh?: boolean } = {}) {
    const place = props.topic
    if (!place) {
      boardRows.value = []
      return
    }
    const roomId = place.id
    // 切回来过的房间先画上次那一份，背后再重取：不转圈、不闪空。
    const cached = cachedTopicPanel('roomTasks', roomId)
    if (cached && boardFor !== roomId) {
      boardRows.value = cached.data
      boardFor = roomId
    }
    boardLoading.value = true
    boardError.value = null
    try {
      // 每条活只带最新那一块（limit 在 lib/topicPanelCache.ts 里定死）：这里只要它来算
      // 「最后活动」，不传的话一个跑久了的房间会把每条支线的完整历史都吐回来。
      const payload = await fetchRoomTasks(roomId, opts)
      if (props.topic?.id !== roomId) return
      boardRows.value = payload.data
      boardFor = roomId
    } catch {
      if (props.topic?.id === roomId) boardError.value = t('work.room.taskProgress.loadFailed')
    } finally {
      boardLoading.value = false
    }
  }

  watch(
    () => [props.active, props.refreshTick] as const,
    ([isActive, tick], before) => {
      // 一轮刚结束（tick 变了）：不跟着那之前发出去的请求走。
      if (isActive) void loadBoard({ fresh: !!before && tick !== before[1] })
    },
    { immediate: true }
  )

  // ---- 上一轮的进度清单 ----
  const progressItems = ref<TodoItem[]>([])

  async function loadProgress(opts: { fresh?: boolean } = {}) {
    const tid = props.topic?.id
    if (!tid) {
      progressItems.value = []
      return
    }
    // 切回来过的房间先画上次那份清单，背后再重取。
    const cached = cachedTopicPanel('progress', tid)?.items
    if (cached) progressItems.value = cached
    try {
      const progress = await fetchTopicProgress(tid, opts)
      if (props.topic?.id === tid) progressItems.value = progress.items ?? []
    } catch {
      // 进度是背景信息，拿不到就不画，不为它报错。
    }
  }

  void loadProgress()
  watch(
    () => props.refreshTick,
    () => void loadProgress({ fresh: true })
  )

  // ---- 这个房间里的东西 ----
  const outputItems = ref<RoomOutput[]>([])
  const outputTemplates = ref<DocumentTemplate[]>([])

  async function loadOutputs() {
    const topicId = props.topic?.id
    if (!topicId) {
      outputItems.value = []
      return
    }
    try {
      outputItems.value = (await listRoomOutputs(topicId)).data
    } catch {
      // 读不到这一块就不显示列表：这一格的主体是上面的文档。
      outputItems.value = []
    }
  }

  function loadOutputTemplates() {
    const topicId = props.topic?.id
    if (!topicId || outputTemplates.value.length) return
    listDocumentTemplates(topicId)
      .then((listed) => {
        outputTemplates.value = listed.data
      })
      .catch(() => {
        outputTemplates.value = []
      })
  }

  async function saveOutputToLibrary(path: string): Promise<string> {
    const topicId = props.topic?.id
    if (!topicId) return ''
    return (await saveRoomOutputToLibrary(topicId, path)).name
  }

  /** 建完就地刷新列表，把新文件的路径交回去——开成页签的那一下是渲染那一层的事。 */
  async function createOutputFromTemplate(templateId: string, path: string): Promise<string> {
    const topicId = props.topic?.id
    if (!topicId) return ''
    const made = await newFromTemplate(topicId, templateId, path)
    await loadOutputs()
    return made.path
  }

  void loadOutputs()
  watch(
    () => props.topic?.id,
    () => void loadOutputs()
  )
  watch(
    () => props.refreshTick,
    () => void loadOutputs()
  )

  return {
    boardRows,
    boardLoading,
    boardError,
    progressItems,
    outputItems,
    outputTemplates,
    loadOutputTemplates,
    saveOutputToLibrary,
    createOutputFromTemplate,
  }
}
