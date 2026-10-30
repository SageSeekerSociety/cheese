// 这间房此刻的动静：谁在打字、哪位队友在干活、干到哪一步。
//
// 状态本身见 room/composables/useRoomActivity 和 useLiveSteps，拼成输入框下面那一行
// 的数据见 useActivityLines。往上递给页面（`activity` 事件）是这里的事，所以三件一起
// 建、一起递：页面拿到的那一行和面板自己画的那一行必须同源。
import type { Ref } from 'vue'
import type { useRoomRoster } from '../../components/room/composables/useRoomRoster'
import type { useRoomTurns } from '../../components/room/composables/useRoomTurns'
import type { useRunRecords } from '../../components/room/composables/useRunRecords'
import type { Block } from '../../cx_types'
import type { ChatPanelEmit } from '../chatPanelContract'
import type { usePanelSocket } from './socket'

import { watch } from 'vue'

import { useActivityLines } from '../../components/room/composables/useActivityLines'
import { useLiveSteps } from '../../components/room/composables/useLiveSteps'
import { useRoomActivity } from '../../components/room/composables/useRoomActivity'
import { isAgentHandle } from '../../lib/authorship'

/** 房间里此刻的动静。 */
export function usePanelActivity(deps: {
  /** 我自己的 handle：我打字也一样报上去。 */
  author: string
  emit: ChatPanelEmit
  /** 说话的那条路（usePanelSocket）：打字通知从它走。 */
  socket: ReturnType<typeof usePanelSocket>
  /** 名册：把 handle 说成人名。 */
  roster: ReturnType<typeof useRoomRoster>
  /** 此刻在跑的那几轮（useRoomTurns）：那一行顺带写它到哪一步了。 */
  turns: ReturnType<typeof useRoomTurns>
  /** 排队等着跑的那几件（useRunRecords）：还没轮到也算在忙。 */
  runRecords: ReturnType<typeof useRunRecords>
  /** 屏幕上的对话：平台排了自动重试的那几位从这里读。 */
  messages: Ref<Block[]>
}) {
  // 此刻谁在这个房间里忙（打字的人、干活的队友）—— 见 room/composables/useRoomActivity；
  // 拼成输入框下面那一行的数据见 useActivityLines（阶段、当前一步）。
  const activity = useRoomActivity({ me: deps.author, send: deps.socket.sendOnSocket })
  const liveSteps = useLiveSteps()
  const activityLines = useActivityLines(
    activity.others,
    liveSteps,
    (handle) =>
      deps.roster.agentNameOf(handle) ??
      (isAgentHandle(handle)
        ? deps.roster.agentDisplayName(handle)
        : deps.roster.memberByHandle.value.get(handle)?.name || handle),
    (handle) => deps.turns.faces.value[handle]?.status
  )
  watch(
    () => {
      const busy = new Set(activityLines.value.map((l) => l.handle))
      const queued = deps.runRecords.waitingLines(
        (h) => deps.roster.agentNameOf(h) ?? deps.roster.agentDisplayName(h),
        deps.messages.value
      )
      return [...activityLines.value, ...queued.filter((l) => !busy.has(l.handle))]
    },
    (v) => deps.emit('activity', v)
  )

  return { activity, liveSteps, activityLines }
}
