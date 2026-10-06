// 频道概览要的数据：置顶、这个频道里的任务（各带最后说的一句），综合还要项目总览
// 是哪一份文档。频道说明在频道本身上，由 store 改。
import type { PanelDocument } from '@/composables/usePanelDoc'
import type { RoomTask } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'

import { ref, watch } from 'vue'

import { listRoomTasks } from '@/api'
import { listPins, unpinBlock } from '@/api/pins'
import { getProjectOverview } from '@/api/projectDocuments'

export function useChannelOverview(opts: {
  /** 正看着的频道；任务页上是 null，什么都不读。 */
  channelId: () => string | null
  projectId: () => string
  /** 这个频道是不是综合。 */
  general: () => boolean
  reportError: (e: unknown) => void
}) {
  const tasks = ref<RoomTask[]>([])
  const pins = ref<ChannelPin[]>([])
  const overview = ref<PanelDocument | null>(null)

  async function loadTasks() {
    const id = opts.channelId()
    if (!id) return
    try {
      const listed = await listRoomTasks(id, { limit: 1 })
      if (opts.channelId() === id) tasks.value = listed.data
    } catch {
      // 任务列表是概览的一块；读不到就先空着，下一次动静会再读。
    }
  }
  async function loadPins() {
    const id = opts.channelId()
    if (!id) return
    try {
      const listed = await listPins(id)
      if (opts.channelId() === id) pins.value = listed
    } catch {
      // 同上。
    }
  }
  async function loadOverview() {
    if (!opts.general()) {
      overview.value = null
      return
    }
    try {
      const { id } = await getProjectOverview(opts.projectId())
      overview.value = { id, projectId: opts.projectId(), title: '' }
    } catch {
      overview.value = null
    }
  }

  watch(
    () => [opts.channelId(), opts.general()] as const,
    ([id]) => {
      tasks.value = []
      pins.value = []
      if (!id) return
      void loadTasks()
      void loadPins()
      void loadOverview()
    },
    { immediate: true }
  )

  async function unpin(blockId: string) {
    const id = opts.channelId()
    if (!id) return
    try {
      await unpinBlock(id, blockId)
      pins.value = pins.value.filter((p) => p.block.id !== blockId)
    } catch (e) {
      opts.reportError(e)
    }
  }

  return { tasks, pins, overview, loadTasks, loadPins, unpin }
}
