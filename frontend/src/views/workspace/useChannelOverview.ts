// 频道概览要的数据：置顶、这个频道里的任务（各带最后说的一句），综合还要项目总览
// 是哪一份文档。频道说明在频道本身上，由 store 改。
import type { PanelDocument } from '@/composables/usePanelDoc'
import type { RoomTask } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'

import { computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { unpinBlock } from '@/api/pins'
import { RECENT_DONE } from '@/lib/channelTasks'
import { patchQuery, queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { overviewQuery } from '@/query/project'
import { openRoomTasksQuery, pinsQuery, roomTasksQuery } from '@/query/room'

export function useChannelOverview(opts: {
  /** 正看着的频道；任务页上是 null，什么都不读。 */
  channelId: () => string | null
  projectId: () => string
  /** 这个频道是不是综合。 */
  general: () => boolean
  /** 频道里有了动静（一轮做完、文档变了）：项目总览的正文跟着重读。 */
  tick: () => number
  reportError: (e: unknown) => void
}) {
  const channel = () => opts.channelId() ?? ''
  // 概览画的是还开着的，加上最近做完的几件：已经做完的是开着的十几倍，不整份读。只要
  // 任务本身（limit: 0），不看任何一个块。任务列表是概览的一块；读不到就先空着，下一次
  // 动静会再读。
  const openRead = useQuery(
    computed(() => ({ ...openRoomTasksQuery(channel()), enabled: !!opts.channelId() })),
    queryClient
  )
  const doneRead = useQuery(
    computed(() => ({
      ...roomTasksQuery(channel(), { limit: 0, status: 'closed', latest: RECENT_DONE }),
      enabled: !!opts.channelId(),
    })),
    queryClient
  )
  const tasks = computed<RoomTask[]>(() => [...(openRead.data.value?.data ?? []), ...(doneRead.data.value?.data ?? [])])
  const pinsRead = useQuery(
    computed(() => ({ ...pinsQuery(channel()), enabled: !!opts.channelId() })),
    queryClient
  )
  const pins = computed<ChannelPin[]>(() => pinsRead.data.value ?? [])
  /** 综合里还有项目总览：概览里只读地显示开头，要改就整份打开。 */
  const overviewRead = useQuery(
    computed(() => ({ ...overviewQuery(opts.projectId()), enabled: !!opts.channelId() && opts.general() })),
    queryClient
  )
  const overview = computed<PanelDocument | null>(() => {
    const id = opts.general() ? overviewRead.data.value?.id : null
    return id ? { id, projectId: opts.projectId(), title: '' } : null
  })
  const overviewText = computed(() => (opts.general() ? overviewRead.data.value?.text ?? '' : ''))

  watch(opts.tick, () => {
    if (opts.general()) void queryClient.invalidateQueries({ queryKey: keys.projectOverview(opts.projectId()) })
  })

  async function unpin(blockId: string) {
    const id = opts.channelId()
    if (!id) return
    try {
      await unpinBlock(id, blockId)
      await patchQuery<ChannelPin[]>(keys.roomPins(id), (held) => held.filter((p) => p.block.id !== blockId))
    } catch (e) {
      opts.reportError(e)
    }
  }

  return { tasks, pins, overview, overviewText, unpin }
}
